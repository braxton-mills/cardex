"""Subject timelapses: `campi focus "water tower" --box x0,y0,x1,y1` follows one part of the scene over the whole
history (venv-sightings: numpy, cv2, PIL, google-genai), in the crime-lab style of forensic.py.

- Daylight only (exposure, else luma), spread evenly over daylight time; nights are skipped.
- Every frame is registered to a reference (ORB features on the static background: tree line, poles, ground;
  rotation, scale and shift, smoothed over neighbours), so the subject holds still even where the camera was
  re-aimed. The box (camera pixels at the reference) is cut out and upscaled to the output size.
- Gemini surveys one frame a day (the one nearest midday): where the subject is, its stage, what changed since the
  day before, the equipment on site, and how far along it looks. The feed tracks the subject's box, charts its height
  day by day (from Gemini's boxes, never shrinking) and shows the estimated completion.
- Each day freezes on that survey frame: reticle, ENHANCE, a PiP that wipes between the previous day and today, and a
  dossier. It ends on a full-frame first-day vs last-day comparison.

The result is a custom timelapse job (state\\custom\\), so it is on the Timelapse tab.
"""
from __future__ import annotations

import bisect
import logging
import math
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import custom_jobs as cj
from . import frames as fr
from . import render_index
from .annotate import captured, state_dir
from .config import NO_WINDOW, read_json, write_json
from .forensic import (AMBER, CYAN, FREEZE_S, GREEN, PLAY_AT, RED, TYPE_CPS, WHITE, Freeze, Kit, Moment, blend,
                       brackets, ease, glitch, span, synth)
from .render import ffmpeg_cmd, nvenc_available

log = logging.getLogger("focus")

DAY_EXPOSURE = 0.02            # s x gain at or below this = daylight (rows without exposure: luma)
DAY_LUMA = 70.0
FPS = 30
END_S = 5.0                    # the closing first-day vs last-day comparison
MIN_INLIERS = 45               # background matches a registration needs to count


# --- frames: daylight, registered -----------------------------------------------------------------------------------

def daylight(rows: list[fr.Frame]) -> list[fr.Frame]:
    return [f for f in rows if (f.exposure <= DAY_EXPOSURE if f.exposure is not None else f.luma >= DAY_LUMA)]


class Register:
    """Similarity transforms that map each frame onto the reference frame: SIFT matches on the background (RANSAC),
    then refined with ECC on the same background so the subject doesn't creep."""

    def __init__(self, ref: np.ndarray, box: tuple[int, int, int, int]):
        self.sift = cv2.SIFT_create(4000)
        self.bf = cv2.BFMatcher(cv2.NORM_L2)
        self.s = 0.5
        g = cv2.resize(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), None, fx=self.s, fy=self.s)
        self.mask = self.background_mask(g.shape, box)
        self.kp, self.des = self.sift.detectAndCompute(g, self.mask)
        self.e = 0.25  # ECC works at quarter size
        self.ref_e = cv2.GaussianBlur(cv2.resize(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), None, fx=self.e, fy=self.e),
                                      (0, 0), 1.0).astype(np.float32)
        self.mask_e = cv2.resize(self.mask, (self.ref_e.shape[1], self.ref_e.shape[0]),
                                 interpolation=cv2.INTER_NEAREST)
        self.inliers = 0

    def background_mask(self, shape, box) -> np.ndarray:
        """Below the skyline (clouds move), minus the subject itself (it changes)."""
        h, w = shape
        m = np.zeros((h, w), np.uint8)
        m[int(h * 0.42):int(h * 0.80)] = 255
        x0, y0, x1, y1 = (int(v * self.s) for v in box)
        m[max(0, y0 - 20):y1, max(0, x0 - 20):x1 + 20] = 0
        return m

    def __call__(self, img: np.ndarray) -> np.ndarray | None:
        """2x3 matrix from img to reference pixels, or None when there aren't enough good matches."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(gray, None, fx=self.s, fy=self.s)
        kp, des = self.sift.detectAndCompute(g, None)
        self.inliers = 0
        if des is None or len(kp) < 30:
            return None
        pairs = self.bf.knnMatch(des, self.des, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.72 * p[1].distance]
        if len(good) < 30:
            return None
        src = np.float32([kp[m.queryIdx].pt for m in good]) / self.s
        dst = np.float32([self.kp[m.trainIdx].pt for m in good]) / self.s
        M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0,
                                             maxIters=4000, confidence=0.999)
        if M is None or inl is None or inl.sum() < 30:
            return None
        self.inliers = int(inl.sum())
        # refine: ECC maps the reference onto the frame, so start from the inverse and invert back
        try:
            inv = cv2.invertAffineTransform(M)
            W = inv.copy()
            W[:, 2] *= self.e
            q = cv2.GaussianBlur(cv2.resize(gray, None, fx=self.e, fy=self.e), (0, 0), 1.0).astype(np.float32)
            _, W = cv2.findTransformECC(self.ref_e, q, W.astype(np.float32), cv2.MOTION_AFFINE,
                                        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), self.mask_e, 3)
            W = W.astype(np.float64)
            W[:, 2] /= self.e
            R = cv2.invertAffineTransform(W)
            if np.abs(R[:, 2] - M[:, 2]).max() < 40 and np.abs(R[:, :2] - M[:, :2]).max() < 0.05:
                M = R
        except cv2.error:
            pass
        return M


def params(M: np.ndarray) -> np.ndarray:
    """(angle, log scale, tx, ty) of a similarity matrix, for smoothing."""
    a, b = M[0, 0], M[1, 0]
    return np.array([math.atan2(b, a), math.log(math.hypot(a, b)), M[0, 2], M[1, 2]])


def matrix(p: np.ndarray) -> np.ndarray:
    s = math.exp(p[1])
    c, si = s * math.cos(p[0]), s * math.sin(p[0])
    return np.array([[c, -si, p[2]], [si, c, p[3]]], np.float64)


def smooth(ms: list[np.ndarray | None], radius: int = 4) -> list[np.ndarray]:
    """Fill failed registrations from neighbours, then a running median per parameter (no jitter, still follows a
    camera that was moved: a step stays a step)."""
    ps = [params(m) if m is not None else None for m in ms]
    have = [i for i, p in enumerate(ps) if p is not None]
    if not have:
        return [np.float64([[1, 0, 0], [0, 1, 0]])] * len(ms)
    for i, p in enumerate(ps):
        if p is None:
            j = min(have, key=lambda j: abs(j - i))
            ps[i] = ps[j]
    arr = np.array(ps)
    out = []
    for i in range(len(arr)):
        out.append(matrix(np.median(arr[max(0, i - radius):i + radius + 1], axis=0)))
    return out


def view(img: np.ndarray, M: np.ndarray, box, w: int, h: int) -> np.ndarray:
    """The subject box (reference pixels) of a frame, registered and upscaled to w x h, lightly sharpened."""
    x0, y0, x1, y1 = box
    S = np.array([[w / (x1 - x0), 0, -x0 * w / (x1 - x0)], [0, h / (y1 - y0), -y0 * h / (y1 - y0)], [0, 0, 1]])
    A = S @ np.vstack([M, [0, 0, 1]])
    out = cv2.warpAffine(img, A[:2], (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    return cv2.addWeighted(out, 1.5, cv2.GaussianBlur(out, (0, 0), 1.6), -0.5, 0)


# --- Gemini survey --------------------------------------------------------------------------------------------------

SURVEY_PROMPT = """You are the analysis computer in a TV crime drama, surveying a construction site from a fixed,
stabilized camera. The subject is the {subject}. {frames} Give:
- stage: the {subject}'s construction stage today, at most 5 words,
- progress: your estimate of percent complete today, 0-100,
- changes: up to 3 short lines (at most 40 characters, capitals) on what visibly changed on the {subject} and around it
  {since},
- equipment: up to 3 items of equipment or vehicles on site today, in capitals,
- readout: 3 short lines (at most 40 characters, capitals) a forensic computer would type, playful but grounded in what
  you can see; no times or dates."""


def survey(cfg, subject: str, images: list[np.ndarray], days: list[str], cache: Path) -> list[dict]:
    """Gemini on each day's survey frame, next to the previous day's (cached per run)."""
    have = read_json(cache) if cache.is_file() else None
    if isinstance(have, list) and len(have) == len(images) and all("box_2d" not in d for d in have):
        return have
    from google import genai
    from google.genai import types
    from pydantic import BaseModel

    from .sightings_cloud import THINKING

    class Day(BaseModel):
        stage: str
        progress: int
        changes: list[str]
        equipment: list[str]
        readout: list[str]

    def part(im):
        return types.Part.from_bytes(data=cv2.imencode(".jpg", cv2.resize(im, (960, 720)),
                                                       [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes(),
                                     mime_type="image/jpeg")

    client = genai.Client(api_key=Path(cfg.sightings.cloud.api_key_file).read_text(encoding="utf-8").strip(),
                          http_options=types.HttpOptions(timeout=120_000))
    out, think_i = [], 0
    for i, im in enumerate(images):
        if i:
            frames = f"The first image is {days[i - 1]}, the second is {days[i]} (today), same framing."
            since, contents = f"since {days[i - 1]}", [part(images[i - 1]), part(im)]
        else:
            frames = f"The image is {days[i]}, the first day surveyed."
            since, contents = "(first day: describe what is there)", [part(im)]
        prompt = SURVEY_PROMPT.format(subject=subject, frames=frames, since=since)
        while True:
            think = THINKING[think_i]
            try:
                resp = client.models.generate_content(
                    model=cfg.sightings.cloud.model, contents=[*contents, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", response_schema=Day, temperature=0,
                        thinking_config=types.ThinkingConfig(**think) if think else None))
                break
            except Exception as e:
                if "think" in str(e).lower() and think_i < len(THINKING) - 1:
                    think_i += 1
                    continue
                raise
        d = resp.parsed.model_dump() if resp.parsed else {"stage": "", "progress": 0, "changes": [],
                                                          "equipment": [], "readout": []}
        out.append(d)
    write_json(cache, out)
    log.info("gemini surveyed %d days", len(out))
    return out


def ruler_tops(cfg, subject: str, images: list[np.ndarray], cache: Path) -> list[float | None]:
    """Gemini reads the top of the subject's walls off a labelled ruler drawn on each view (vision models read rulers
    far better than they estimate coordinates); view pixels, cached."""
    have = read_json(cache) if cache.is_file() else None
    if isinstance(have, list) and len(have) == len(images):
        return have
    from google import genai
    from google.genai import types
    from pydantic import BaseModel

    class Top(BaseModel):
        top_y: int

    client = genai.Client(api_key=Path(cfg.sightings.cloud.api_key_file).read_text(encoding="utf-8").strip(),
                          http_options=types.HttpOptions(timeout=60_000))
    out = []
    for img in images:
        h, w = img.shape[:2]
        r = img.copy()
        for y in range(100, h, 25):
            major = y % 100 == 0
            cv2.line(r, (0, y), (w if major else 60, y), (0, 0, 255) if major else (0, 200, 255), 2 if major else 1)
            if major:
                cv2.putText(r, str(y), (8, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
        sm = cv2.resize(r, (w * 3 // 4, h * 3 // 4))
        try:
            resp = client.models.generate_content(
                model=cfg.sightings.cloud.model,
                contents=[types.Part.from_bytes(data=cv2.imencode(".jpg", sm)[1].tobytes(), mime_type="image/jpeg"),
                          f"The image has a red ruler: horizontal lines labelled with y values every 100 px (thin "
                          f"ticks every 25 px). Find the {subject}. What is the y value of the top edge of its walls "
                          f"(ignore cranes, booms or equipment on top)? Interpolate between lines."],
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Top,
                                                   temperature=0))
            out.append(float(resp.parsed.top_y) if resp.parsed else None)
        except Exception as e:
            log.warning("ruler reading failed: %s", e)
            out.append(None)
    write_json(cache, out)
    return out


def snap(img: np.ndarray, y_est: float, sub) -> float:
    """Move a reading onto the nearest strong horizontal brightness edge across the middle of the subject (Gemini
    reads a little high: search mostly below its answer)."""
    h, w = img.shape[:2]
    xa, xb = int(w * (sub[0] + (sub[2] - sub[0]) * 0.3)), int(w * (sub[0] + (sub[2] - sub[0]) * 0.75))
    rv = cv2.cvtColor(img[:, xa:xb], cv2.COLOR_BGR2GRAY).astype(np.float32).mean(axis=1)
    e = int(y_est)
    best, by = 0.0, e
    for y in range(max(16, e - 10), min(h - 16, e + 75)):
        step = abs(rv[y:y + 15].mean() - rv[y - 15:y].mean())
        if step > best:
            best, by = step, y
    return by / h


def tidy(days: list[dict], tops: list[float], sub) -> None:
    """Measured heights (never shrinking: a dip is a measuring miss, not demolition), tracked boxes, and progress
    that never drops."""
    x0, _, x1, base = sub
    best_h, best_p = 0.0, 0
    for d, top in zip(days, tops):
        best_h = max(best_h, base - top)
        d["height"] = best_h
        d["box"] = (x0, base - best_h, x1 - x0, best_h)
        best_p = max(best_p, int(d.get("progress") or 0))
        d["progress"] = best_p


# --- the overlay ----------------------------------------------------------------------------------------------------

class SiteFeed:
    def __init__(self, kit: Kit, subject: str, sample_ts: list[float], day_keys: list[date], days: list[dict]):
        self.kit, self.subject, self.ts, self.day_keys, self.days = kit, subject.upper(), sample_ts, day_keys, days
        self.day_of = {d: i for i, d in enumerate(day_keys)}
        w, h, k = kit.w, kit.h, kit.k
        self.bar_h = round(92 * k)
        self.bar_y = h - self.bar_h
        self.px0, self.px1 = round(40 * k), w - round(40 * k)
        self.py0, self.py1 = self.bar_y + round(16 * k), self.bar_y + round(50 * k)
        base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(base)
        d.rectangle([0, self.bar_y, w, h], fill=(4, 10, 16, 255))
        d.line([0, self.bar_y, w, self.bar_y], fill=(*CYAN, 255), width=1)
        lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        m = round(18 * k)
        brackets(ld, m, m, w - m, self.bar_y - m, round(46 * k), (*CYAN, 170), max(2, round(2 * k)))
        # the sequence by day: one segment per day (frames, not hours: nights are skipped), height as its fill
        n = self.n = len(sample_ts)
        for i, dk in enumerate(day_keys):
            idx = [j for j, t in enumerate(sample_ts) if datetime.fromtimestamp(t).date() == dk]
            if not idx:
                continue
            xa, xb = self.xi(idx[0]), self.xi(idx[-1] + 1)
            hh = days[i]["height"] / max(1e-6, days[-1]["height"])
            ld.rectangle([xa + 2, self.py1 - (self.py1 - self.py0) * hh, xb - 2, self.py1], fill=(*CYAN, 140))
            ld.text(((xa + xb) / 2, self.py1 + round(18 * k)), f"DAY {i + 1:02d} {dk:%m-%d}", font=kit.mono_s,
                    fill=(*CYAN, 220), anchor="mm")
        base.alpha_composite(lay)
        self.base = base

    def xi(self, i: float) -> float:
        return self.px0 + i / max(1, self.n) * (self.px1 - self.px0)

    def day_values(self, ts: float):
        """Box, height, progress for a moment, interpolated between the daily surveys."""
        dk = datetime.fromtimestamp(ts).date()
        i = self.day_of.get(dk, 0)
        d = self.days[i]
        return i, d

    def frame(self, i_sample: int, t_out: float) -> Image.Image:
        kit, k, w, h = self.kit, self.kit.k, self.kit.w, self.kit.h
        ts = self.ts[min(i_sample, len(self.ts) - 1)]
        img = self.base.copy()
        d = ImageDraw.Draw(img)
        dt = datetime.fromtimestamp(ts)
        di, day = self.day_values(ts)
        m = round(46 * k)
        d.text((m, round(38 * k)), f"SITE SURVEY // SUBJECT: {self.subject}", font=kit.mono_s, fill=(*CYAN, 230))
        d.text((m, round(60 * k)), f"{dt:%a %Y-%m-%d}".upper(), font=kit.mono_l, fill=(*WHITE, 255))
        d.text((m - 3, round(90 * k)), f"{dt:%H:%M:%S}", font=kit.clock, fill=(*WHITE, 255))
        d.text((m, round(172 * k)), f"DAY {di + 1:02d} OF {len(self.days):02d} // DAYLIGHT ONLY", font=kit.mono,
               fill=(*GREEN, 230))
        rx = w - m
        if int(t_out * 2) % 2 == 0:
            d.ellipse([rx - round(110 * k), round(40 * k), rx - round(94 * k), round(56 * k)], fill=(*RED, 255))
        d.text((rx, round(36 * k)), "REC", font=kit.mono_l, fill=(*RED, 255), anchor="ra")
        growth = day["height"] / max(1e-6, self.days[0]["height"]) - 1
        d.text((rx, round(78 * k)), "STRUCTURE HEIGHT VS DAY 01", font=kit.mono_s, fill=(*CYAN, 230), anchor="ra")
        d.text((rx, round(98 * k)), f"{growth:+.0%}", font=kit.count, fill=(*WHITE, 255), anchor="ra")
        d.text((rx, round(152 * k)), f"EST. COMPLETE {day['progress']:3d}%", font=kit.mono, fill=(*GREEN, 230),
               anchor="ra")
        bw = round(260 * k)
        d.rectangle([rx - bw, round(182 * k), rx, round(194 * k)], outline=(*GREEN, 255))
        d.rectangle([rx - bw + 2, round(184 * k), rx - bw + 2 + (bw - 4) * day["progress"] / 100, round(192 * k)],
                    fill=(*GREEN, 255))
        # stage, and what changed, under the counters
        lines = [f"STAGE: {str(day.get('stage') or '').upper()[:30]}"] + \
                [f"+ {c.upper()[:34]}" for c in (day.get("changes") or [])[:3]]
        for j, line in enumerate(lines):
            d.text((rx, round(214 * k) + j * round(25 * k)), line, font=kit.mono_s,
                   fill=(*(AMBER if j == 0 else WHITE), 235), anchor="ra")
        # the subject, tracked
        bx, by, bw_, bh = day["box"]
        x0, y0, x1, y1 = bx * w, by * h, (bx + bw_) * w, (by + bh) * h
        col = (*CYAN, 200)
        brackets(d, x0, y0, x1, y1, round(24 * k), col, max(2, round(2 * k)))
        d.text((x0, y0 - round(26 * k)), f"TRACKING // {self.subject}", font=kit.mono_s, fill=col)
        # playhead
        xp = self.xi(i_sample)
        d.line([xp, self.py0 - round(10 * k), xp, self.py1 + round(3 * k)], fill=(*WHITE, 255),
               width=max(2, round(2 * k)))
        return img


def ending(kit: Kit, first: np.ndarray, last: np.ndarray, labels: tuple[str, str], growth: float, subject: str,
           t: float, split: float = 0.5) -> np.ndarray:
    """First day vs last day: a wipe sweeps right to left, then settles on split (the subject's middle, so it
    shows half then, half now), then the verdict."""
    w, h, k = kit.w, kit.h, kit.k
    if t < 2.2:
        xw = int(w * (0.95 - 0.9 * ease(span(t, 0.3, 2.2))))
    else:
        xw = int(w * (0.05 + (split - 0.05) * ease(span(t, 2.2, 3.2))))
    out = last.copy()
    out[:, :xw] = first[:, :xw]
    out = kit.apply_grade(out)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle([xw - 2, 0, xw + 2, h], fill=(*AMBER, 255))
    m = round(46 * k)
    d.text((m, round(36 * k)), "COMPARATIVE ANALYSIS // " + subject.upper(), font=kit.mono_l, fill=(*RED, 255))
    d.text((m, round(80 * k)), labels[0], font=kit.title_l, fill=(*WHITE, 255))
    d.text((w - m, round(80 * k)), labels[1], font=kit.title_l, fill=(*WHITE, 255), anchor="ra")
    if t >= 3.4:
        a = int(255 * span(t, 3.4, 3.7))
        # the verdict on the side away from the subject, high up (the sky), so the split stays visible
        cx = w * (0.72 if split < 0.5 else 0.28)
        cy = h * 0.3
        d.rectangle([cx - round(330 * k), cy - round(80 * k), cx + round(330 * k), cy + round(80 * k)],
                    fill=(4, 10, 16, int(a * 0.85)), outline=(*CYAN, a), width=2)
        d.text((cx, cy - round(28 * k)), f"HEIGHT {growth:+.0%}", font=kit.title_l, fill=(*GREEN, a), anchor="mm")
        d.text((cx, cy + round(36 * k)), "CONSTRUCTION CONTINUES // MONITORING ACTIVE", font=kit.mono,
               fill=(*CYAN, a), anchor="mm")
    out = blend(out, ov)
    if t < 0.22:
        out = glitch(out, 1 - t / 0.22, int(t * 1000))
    if t > END_S - 0.6:
        out = (out * (1 - span(t, END_S - 0.6, END_S))).astype(np.uint8)
    return out


# --- the pass -------------------------------------------------------------------------------------------------------

def run(cfg, subject: str, box: tuple[int, int, int, int], start: float | None = None, end: float | None = None,
        seconds: float = 50.0, ai: bool = True, preview: float | None = None,
        subject_box: tuple[int, int, int, int] | None = None) -> dict:
    """box: the view (camera pixels at the reference frame, 4:3); subject_box: the structure inside it (x0, the
    highest it may reach, x1, its base), measured and tracked; default the middle of the view."""
    t_start = time.time()
    w, h = int(cfg.output.width), int(cfg.output.height)
    end = end or time.time()
    if start is None:
        dirs = sorted(p.name for p in cfg.paths.frames.iterdir() if p.is_dir() and p.name[:2] == "20")
        start = datetime.fromisoformat(dirs[0]).timestamp() if dirs else end - 7 * 86400
    rows = daylight(captured(cfg, start, end))
    rows = [f for f in rows if f.path.exists()]
    if len(rows) < 30:
        raise ValueError("not enough daylight frames in that range")
    # register everything onto the frame nearest midday of the last day
    last_day = datetime.fromtimestamp(rows[-1].ts).date()
    last_noon = datetime.combine(last_day, datetime.min.time()).timestamp() + 13 * 3600
    ref_f = min(rows, key=lambda f: abs(f.ts - last_noon))
    reg = Register(cv2.imread(str(ref_f.path), cv2.IMREAD_COLOR), box)
    # where the camera was somewhere else (re-aimed, tilted) nothing registers: start once it reliably does
    probe = fr.sample_by_time(rows, 160)
    ok = [reg(cv2.imread(str(f.path), cv2.IMREAD_COLOR)) is not None and reg.inliers >= MIN_INLIERS for f in probe]
    first = next((i for i in range(len(probe)) if all(ok[i:i + 6])), 0)
    if first:
        log.info("the camera only matches the reference from %s; starting there",
                 datetime.fromtimestamp(probe[first].ts).isoformat(timespec="minutes"))
        rows = [f for f in rows if f.ts >= probe[first].ts]
    sel = fr.sample_by_time(rows, round(seconds * FPS))
    if preview:
        sel = sel[:round(preview * FPS)]
    days_all = sorted({datetime.fromtimestamp(f.ts).date() for f in rows})
    stem = f"focus_{subject.lower().replace(' ', '-')}_{days_all[0]:%Y%m%d}"
    sd = state_dir(cfg)
    t0 = time.time()
    reg_cache = sd / f"{stem}.registration.json"
    known = read_json(reg_cache) if reg_cache.is_file() else None
    known = known if isinstance(known, dict) and known.get("box") == list(box) and known.get("ref") == ref_f.path.name \
        else {"box": list(box), "ref": ref_f.path.name, "m": {}}
    mats = []
    for f in sel:
        if f.path.name in known["m"]:
            v = known["m"][f.path.name]
            mats.append(np.array(v, np.float64) if v else None)
            continue
        M = reg(cv2.imread(str(f.path), cv2.IMREAD_COLOR))
        M = M if M is not None and reg.inliers >= MIN_INLIERS else None
        known["m"][f.path.name] = M.tolist() if M is not None else None
        mats.append(M)
    write_json(reg_cache, known)
    log.info("registered %d frames in %.0f s (%d failed)", len(sel), time.time() - t0, sum(m is None for m in mats))
    mats = smooth(mats)

    # one survey frame a day: the daylight frame nearest 13:00 (the middle of the day's light when that's later)
    work = cfg.paths.work / stem
    work.mkdir(parents=True, exist_ok=True)
    day_keys, survey_frames, survey_mats = [], [], []
    for dk in days_all:
        day_rows = [f for f in rows if datetime.fromtimestamp(f.ts).date() == dk]
        if len(day_rows) < 30 or day_rows[-1].ts - day_rows[0].ts < 3 * 3600:
            continue  # a sliver of a day (the range starts at dusk) isn't a survey
        noon = datetime.combine(dk, datetime.min.time()).timestamp() + 13 * 3600
        for f in sorted(day_rows, key=lambda f: abs(f.ts - noon))[:40:4]:
            M = reg(cv2.imread(str(f.path), cv2.IMREAD_COLOR))
            if M is not None and reg.inliers >= 2 * MIN_INLIERS:
                day_keys.append(dk)
                survey_frames.append(f)
                survey_mats.append(M)
                break
    survey_imgs, survey_paths = [], []
    for dk, f, M in zip(day_keys, survey_frames, survey_mats):
        img = cv2.imread(str(f.path), cv2.IMREAD_COLOR)
        v = view(img, M, box, w, h)
        p = work / f"survey_{dk:%Y%m%d}.jpg"
        cv2.imwrite(str(p), v, [cv2.IMWRITE_JPEG_QUALITY, 95])
        survey_imgs.append(v)
        survey_paths.append(p)
    labels = [f"{dk:%a %m-%d}".upper() for dk in day_keys]
    if ai:
        days = survey(cfg, subject, survey_imgs, labels, sd / f"{stem}.survey.json")
    else:
        days = [{"stage": "", "progress": 0, "changes": [], "equipment": [], "readout": []} for _ in day_keys]
    # the subject's own area in view coordinates (0-1): what is measured and tracked
    bx0, by0, bx1, by1 = box
    sb = subject_box or (bx0 + (bx1 - bx0) * 0.2, by0, bx0 + (bx1 - bx0) * 0.8, by1)
    sub = ((sb[0] - bx0) / (bx1 - bx0), (sb[1] - by0) / (by1 - by0), (sb[2] - bx0) / (bx1 - bx0),
           (sb[3] - by0) / (by1 - by0))
    readings = ruler_tops(cfg, subject, survey_imgs, sd / f"{stem}.tops.json") if ai else [None] * len(survey_imgs)
    tops = [snap(v, r, sub) if r is not None else sub[1] for v, r in zip(survey_imgs, readings)]
    tidy(days, tops, sub)
    for dk, d, top in zip(day_keys, days, tops):
        log.info("  %s: top %.3f height %.3f, %s, %d%%; %s", dk, top, d["height"], d.get("stage"), d["progress"],
                 " / ".join(d.get("changes") or []))

    moments = []
    for i, (dk, f, p, d) in enumerate(zip(day_keys, survey_frames, survey_paths, days)):
        growth = d["height"] / max(1e-6, days[0]["height"]) - 1
        lines = [f"> SITE SCAN {labels[i]} ................ DONE",
                 f"  STAGE    : {str(d.get('stage') or 'UNKNOWN').upper()[:34]}",
                 f"  PROGRESS : {d['progress']}% (EST.)   HEIGHT {growth:+.0%} VS DAY 01"]
        lines += [f"  CHANGE   : {c.upper()[:40]}" for c in (d.get("changes") or [])[:2]]
        if d.get("equipment"):
            lines.append(f"  ON SITE  : {', '.join(e.upper() for e in d['equipment'][:3])[:40]}")
        lines += [f"  {r.upper()[:44]}" for r in (d.get("readout") or [])[:2]]
        lines.append("> STATUS: " + ("BASELINE ESTABLISHED" if i == 0 else "PROGRESS CONFIRMED"))
        moments.append(Moment(ts=f.ts, kind="ai", image=p, box=d["box"], title="DAILY SURVEY",
                              tag=f"DAY {i + 1:02d} // {labels[i]}", lines=lines,
                              stamp=f"SRC CAM-01 STILL // REGISTERED + 2.8x UPSCALE",
                              compare=survey_paths[i - 1] if i else None,
                              compare_label=labels[i - 1] if i else "", now_label=labels[i]))
    # each freeze goes before the first sampled frame at or after its moment
    sel_ts = [f.ts for f in sel]
    for mo in moments:
        mo.k = bisect.bisect_left(sel_ts, mo.ts)
    moments = [mo for mo in moments if mo.k < len(sel)]

    kit = Kit(w, h)
    feed = SiteFeed(kit, subject, sel_ts, day_keys, days)
    if preview:
        dst = cfg.paths.work / f"{stem}_preview.mp4"
        job = None
    else:
        job = {**cj.new_job(sel[0].ts, sel[-1].ts, None, seconds), "subject": subject, "style": "focus"}
        dst = cfg.paths.custom / cj.file_name(job)
    silent = cfg.paths.work / f"{dst.stem}.video.mp4"
    wav = cfg.paths.work / f"{dst.stem}.wav"
    encoder = "nvenc" if cfg.output.encoder in ("auto", "nvenc") and nvenc_available(cfg) else "x264"
    enc = subprocess.Popen(ffmpeg_cmd(cfg, encoder, w, h, FPS, silent, deflicker=False), stdin=subprocess.PIPE,
                           creationflags=NO_WINDOW)
    fz_n = round(FREEZE_S * FPS)
    out_i, out_ts, freezes_at, typing = 0, [], [], []
    pending = list(moments)
    # brightness: each frame scaled toward a running average of its neighbours (auto exposure flicker)
    lumas = []
    try:
        for i, f in enumerate(sel):
            while pending and pending[0].k <= i:
                mo = pending.pop(0)
                fz = Freeze(kit, mo, len(freezes_at) + 1)
                t0 = out_i / FPS
                freezes_at.append(t0)
                n_chars = sum(len(x) + 4 for x in [mo.title] + mo.lines)
                typing.append((t0 + 1.4, t0 + min(FREEZE_S - 0.3, 1.4 + n_chars / TYPE_CPS)))
                for j in range(fz_n):
                    enc.stdin.write(fz.frame(j / FPS).tobytes())
                    out_ts.append(mo.ts)
                    out_i += 1
                log.info("freeze %d (%s) at %.1f s", len(freezes_at), mo.tag, t0)
            img = cv2.imread(str(f.path), cv2.IMREAD_COLOR)
            v = view(img, mats[i], box, w, h)
            lumas.append(float(v[::8, ::8].mean()))
            target = float(np.mean(lumas[-12:]))
            if lumas[-1] > 1:
                v = np.clip(v.astype(np.float32) * (0.5 + 0.5 * target / lumas[-1]), 0, 255).astype(np.uint8)
            v = kit.apply_grade(v)
            enc.stdin.write(blend(v, feed.frame(i, out_i / FPS)).tobytes())
            out_ts.append(f.ts)
            out_i += 1
            if (i + 1) % 300 == 0:
                log.info("focus %s: %d/%d frames", dst.name, i + 1, len(sel))
        if not preview and len(survey_imgs) >= 2:
            growth = days[-1]["height"] / max(1e-6, days[0]["height"]) - 1
            for j in range(round(END_S * FPS)):
                enc.stdin.write(ending(kit, survey_imgs[0], survey_imgs[-1], (labels[0], labels[-1]), growth,
                                       subject, j / FPS, (sub[0] + sub[2]) / 2).tobytes())
                out_ts.append(sel[-1].ts)
                out_i += 1
    finally:
        enc.stdin.close()
        rc = enc.wait()
    if rc != 0:
        raise RuntimeError(f"encoder exited {rc}")
    total_s = out_i / FPS
    synth(total_s, freezes_at, typing, wav)
    tmp = dst.with_name(dst.stem + ".part.mp4")
    r = subprocess.run([cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent), "-i",
                        str(wav), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags",
                        "+faststart", str(tmp)], creationflags=NO_WINDOW)
    silent.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("muxing the sound failed")
    tmp.replace(dst)
    res = {"output": str(dst), "duration_s": round(total_s, 1), "frames": out_i, "days": len(day_keys),
           "freezes": len(freezes_at), "elapsed_s": round(time.time() - t_start, 1)}
    if job:
        # one index entry per video frame: this video is FPS with no interpolation, unlike the service's renders
        render_index.index_dir(cfg).mkdir(exist_ok=True)
        write_json(render_index.index_path(cfg, dst.name), {
            "output": dst.name, "window_start_ts": sel[0].ts, "window_end_ts": sel[-1].ts, "base_fps": FPS,
            "interp_factor": 1, "frame_ts": [round(t, 3) for t in out_ts]})
        cj.write_job(cfg, {**job, "status": "ok", "progress": 1.0, "output": dst.name, "duration_s": total_s,
                           "frames_used": len(sel), "frames_available": len(rows), "finished": time.time()})
        res["job"] = job["id"]
    log.info("focus %s -> %s (%.0f s, %d freezes, %.0f s)", subject, dst.name, total_s, len(freezes_at),
             res["elapsed_s"])
    return res
