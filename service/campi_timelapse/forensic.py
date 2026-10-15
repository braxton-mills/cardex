"""`campi annotate --style forensic`: the annotated timelapse as a TV crime-lab computer would show it.

Between moments it is a control-room feed: a monospace clock and elapsed time, a blinking REC, the vehicle count and
hourly rate, a live detection log scrolling past, scanlines and a vignette, a timeline bar, and terminal-style event
lines (sunrise/sunset, the busiest hour, day totals, the camera going offline).

At each moment the feed freezes: it glitches, cuts to the frame of the moment, and a cursor clicks the subject. A
reticle locks on, a picture-in-picture "ENHANCE"s the subject from blocks to sharp and then replays the sighting's
own clip, and a dossier types itself out. Then it glitches back to the feed. The moments are your car, the rarest
finds (labels seen exactly once, with their sighting frame, crop and clip), and the 2 things Gemini found most
interesting in the scene notes, with a box it drew around each.

Sound is synthesized (a drone, typing clicks, lock and enhance beeps, glitch noise) and muxed in as AAC. The output
is longer than the source: every freeze adds FREEZE_S.
"""
from __future__ import annotations

import bisect
import json
import logging
import math
import sqlite3
import subprocess
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import custom_jobs as cj
from . import render_index
from .annotate import (MY_CAR, ai_notes, build_events, captured, night_spans, schedule, state_dir, traffic,
                       worth_showing)
from .config import NO_WINDOW, read_json, write_json
from .render import ffmpeg_cmd, ffprobe, nvenc_available

log = logging.getLogger("annotate")

FREEZE_S = 5.2
PLAY_AT = 3.5                    # the PiP replays the sighting clip from here
TYPE_CPS = 140                   # dossier typing speed, characters a second
MAX_RARE, MAX_AI = 3, 2
MIN_GAP_S = 4.0                  # source-video seconds between two moments
CYAN, GREEN, AMBER, RED, WHITE = (70, 225, 255), (90, 255, 170), (255, 196, 70), (255, 64, 88), (235, 245, 255)
MONO = r"C:\Windows\Fonts\CascadiaMono.ttf"
MONO_FALLBACK = r"C:\Windows\Fonts\consola.ttf"
TITLE = r"C:\Windows\Fonts\OCRAEXT.TTF"
SR = 48000


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def span(t: float, a: float, b: float) -> float:
    """0 before a, 1 after b, linear in between."""
    return min(1.0, max(0.0, (t - a) / (b - a)))


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(MONO_FALLBACK, size)


# --- moments ------------------------------------------------------------------------------------------------------

@dataclass
class Moment:
    ts: float
    kind: str                    # mycar | rare | ai
    image: Path
    box: tuple[float, float, float, float] | None   # x, y, w, h in 0-1 of the image
    title: str
    tag: str
    lines: list[str]
    clip: Path | None = None
    compare: Path | None = None  # the same view earlier (same framing): the PiP wipes from it to this one
    compare_label: str = ""
    now_label: str = ""
    stamp: str = ""
    sid: str = ""
    k: int = 0                   # source video frame the freeze goes before
    cache: dict = field(default_factory=dict)


def sighting_rows(cfg, start: float, end: float) -> list[dict]:
    p = cfg.paths.sightings / "sightings.db"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM sightings WHERE kind = 'vehicle' AND frame_path IS NOT NULL AND crop_path IS NOT NULL")]
    finally:
        con.close()
    out = []
    for r in rows:
        ts = datetime.fromisoformat(r["started_at"]).timestamp()
        if start <= ts <= end:
            out.append({**r, "ts": ts})
    return out


def locate(frame_p: Path, crop_p: Path) -> tuple[float, float, float, float] | None:
    """Where a sighting's crop sits in its frame (0-1), by template matching at half size."""
    f = cv2.imread(str(frame_p), cv2.IMREAD_GRAYSCALE)
    c = cv2.imread(str(crop_p), cv2.IMREAD_GRAYSCALE)
    if f is None or c is None or c.shape[0] > f.shape[0] or c.shape[1] > f.shape[1]:
        return None
    fs, cs = f[::2, ::2], c[::2, ::2]
    res = cv2.matchTemplate(fs, cs, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(res)
    if score < 0.5:
        return None
    H, W = fs.shape
    return x / W, y / H, cs.shape[1] / W, cs.shape[0] / H


def car_moment(cfg, r: dict, kind: str, n_seen: int, first_ts: float, total: int) -> Moment | None:
    root = cfg.paths.sightings
    frame_p, crop_p = root / r["frame_path"], root / r["crop_path"]
    if not frame_p.is_file() or not crop_p.is_file():
        return None
    box = locate(frame_p, crop_p)
    alt = ""
    try:
        ru = json.loads(r.get("runner_ups") or "[]")
        if ru:
            alt = f"   ALT {ru[0]['label'].upper()[:18]} {ru[0]['p']:.0%}"
    except (ValueError, KeyError, TypeError):
        pass
    heading = {"LR": "LEFT -> RIGHT", "RL": "RIGHT -> LEFT"}.get(r.get("direction") or "", "UNKNOWN")
    lines = [f"> CROSS-REF {total:,} SIGHTINGS ....... MATCH",
             f"  MAKE     : {(r.get('make') or '?').upper()}",
             f"  MODEL    : {(r.get('model') or r.get('label') or '?').upper()}",
             f"  YEARS    : {r.get('year_range') or 'UNKNOWN'}",
             f"  COLOR    : {(r.get('color') or 'UNKNOWN').upper()}",
             f"  HEADING  : {heading}",
             f"  MATCH    : {100 * (r.get('confidence') or 0):.1f}%{alt}",
             f"  SEEN     : {n_seen}x   FIRST {datetime.fromtimestamp(first_ts):%m-%d %H:%M}"]
    if kind == "mycar":
        title, tag = "OWNER VEHICLE", "KNOWN // FRIENDLY"
        lines.append("> STATUS: IT'S YOUR CAR. WELCOME HOME.")
    else:
        title, tag = "RARE SIGHTING", "1 OF 1"
        lines.append("> STATUS: FIRST AND ONLY SIGHTING ON RECORD")
    clip = root / r["clip_path"] if r.get("clip_path") and (root / r["clip_path"]).is_file() else None
    return Moment(ts=r["ts"], kind=kind, image=frame_p, box=box, title=title, tag=tag, lines=lines, clip=clip,
                  stamp=f"SRC SIGHTING {r['id'][:8].upper()} // CAM-01 STREAM", sid=r["id"])


AI_LOCATE_PROMPT = """You are the analysis computer in a TV crime drama, reviewing frames from a fixed street camera.
Each frame comes with an observation someone made about it. For each frame:
- interest: 1-10, how interesting this would be to zoom into in a fun recap video (a parked forklift, a crane, a
  person, an odd vehicle or heavy rain score high; plain sky or an ordinary road score low),
- label: 1-3 words naming the subject, in capitals,
- box_2d: [ymin, xmin, ymax, xmax] on a 0-1000 scale around that subject (the whole frame for weather),
- readout: 4 short lines (at most 34 characters each, capitals) a forensic computer would type about it, playful but
  grounded in what you can actually see; no times or dates.

{items}"""


def ai_moments(cfg, notes: list[dict], frames_by_ts, cache: Path) -> list[Moment]:
    """Ask Gemini which noted frames are worth a zoom, and where to look; cached per video."""
    cands = [n for n in worth_showing(notes) if n["notable"]]
    if not cands:
        return []
    found = read_json(cache) if cache.is_file() else None
    if not isinstance(found, list):
        from google import genai
        from google.genai import types
        from pydantic import BaseModel

        from .sightings_cloud import THINKING

        class Find(BaseModel):
            index: int
            interest: int
            label: str
            box_2d: list[int]
            readout: list[str]

        parts = []
        for n in cands:
            img = cv2.imread(str(frames_by_ts(n["ts"])), cv2.IMREAD_COLOR)
            h, w = img.shape[:2]
            s = 768 / max(h, w)
            img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
            parts.append(types.Part.from_bytes(data=cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1]
                                               .tobytes(), mime_type="image/jpeg"))
        items = "\n".join(f"{i}: {n['note']}" for i, n in enumerate(cands))
        client = genai.Client(api_key=Path(cfg.sightings.cloud.api_key_file).read_text(encoding="utf-8").strip(),
                              http_options=types.HttpOptions(timeout=90_000))
        think = THINKING[0]
        for think in THINKING:
            try:
                resp = client.models.generate_content(
                    model=cfg.sightings.cloud.model, contents=[*parts, AI_LOCATE_PROMPT.format(items=items)],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", response_schema=list[Find], temperature=0,
                        thinking_config=types.ThinkingConfig(**think) if think else None))
                break
            except Exception as e:  # a model that doesn't take this thinking setting: try the next lowest
                if "think" not in str(e).lower():
                    raise
        found = [{**f.model_dump(), "ts": cands[f.index]["ts"], "note": cands[f.index]["note"]}
                 for f in (resp.parsed or []) if 0 <= f.index < len(cands)]
        write_json(cache, found)
        log.info("gemini: rated %d noted frames for zooms", len(found))
    out = []
    for f in sorted(found, key=lambda f: -f["interest"]):
        if f["interest"] < 6 or len(out) >= MAX_AI:
            continue
        if any(abs(f["ts"] - m.ts) < 6 * 3600 for m in out):
            continue
        y0, x0, y1, x1 = (min(1000, max(0, v)) / 1000 for v in (f["box_2d"] + [0, 0, 1000, 1000])[:4])
        box = (x0, y0, max(0.04, x1 - x0), max(0.04, y1 - y0))
        lines = [f"> SCENE ANALYSIS ............. FLAGGED", f"  OBSERVED : {f['note'].upper()[:40]}"] + \
                [f"  {line.upper()[:44]}" for line in f["readout"][:4]] + ["> SOURCE: FRAME ANALYSIS // GEMINI"]
        out.append(Moment(ts=f["ts"], kind="ai", image=Path(frames_by_ts(f["ts"])), box=box, title="ANOMALY",
                          tag=f["label"].upper()[:22], lines=lines, stamp="SRC TIMELAPSE FRAME // CAM-01 STILL"))
    return out


def pick_moments(cfg, start, end, by, known, notes, frames_by_ts, ai_cache) -> list[Moment]:
    rows = sighting_rows(cfg, start, end)
    total = sum(len(v) for v in by.values())
    out = []
    mine = [r for r in rows if (r.get("label") or "").lower() == MY_CAR]
    if mine:
        best = max(mine, key=lambda r: (r.get("max_box_px") or 0) * (r.get("confidence") or 0))
        ts_all = by.get(MY_CAR) or [best["ts"]]
        m = car_moment(cfg, best, "mycar", len(ts_all), ts_all[0], total)
        if m:
            out.append(m)
    once = {k for k, v in by.items() if len(v) == 1 and not (known.get(k) or {}).get("generic")
            and ((known.get(k) or {}).get("make") or "").lower() not in ("", "n/a", "unknown")}
    rare = sorted((r for r in rows if (r.get("label") or "").lower() in once),
                  key=lambda r: (r.get("yolo_class") != "car",  # cars first: rare trucks are mostly odd labels
                                 -(r.get("max_box_px") or 0) * (r.get("confidence") or 0)))
    for r in rare:
        if sum(m.kind == "rare" for m in out) >= MAX_RARE:
            break
        if any(abs(r["ts"] - m.ts) < 8 * 3600 for m in out):
            continue
        m = car_moment(cfg, r, "rare", 1, r["ts"], total)
        if m and m.box:
            out.append(m)
    if notes:
        try:
            out += ai_moments(cfg, notes, frames_by_ts, ai_cache)
        except Exception as e:
            log.warning("gemini zooms skipped: %s", e)
    out.sort(key=lambda m: m.ts)
    return out


# --- drawing --------------------------------------------------------------------------------------------------------

class Kit:
    """Fonts and the precomputed full-frame layers, at the output size."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        k = self.k = h / 1080
        self.mono_s = font(MONO, round(17 * k))
        self.mono = font(MONO, round(21 * k))
        self.mono_l = font(MONO, round(27 * k))
        self.clock = font(MONO, round(72 * k))
        self.count = font(MONO, round(46 * k))
        self.title = font(TITLE, round(30 * k))
        self.title_l = font(TITLE, round(54 * k))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        vign = 1 - 0.32 * np.clip(r - 0.55, 0, None) ** 1.6
        scan = np.where((np.arange(h) % 3) == 0, 0.9, 1.0).astype(np.float32)[:, None]
        g = (vign * scan)[..., None]
        self.grade = (g * np.array([1.05, 1.0, 0.93], np.float32)).astype(np.float32)  # BGR: a cool teal tint

    def apply_grade(self, frame: np.ndarray) -> np.ndarray:
        return np.clip(frame.astype(np.float32) * self.grade, 0, 255).astype(np.uint8)


def blend(frame: np.ndarray, ov: Image.Image) -> np.ndarray:
    o = np.asarray(ov)
    a = o[..., 3:4].astype(np.uint16)
    rgb = o[..., 2::-1].astype(np.uint16)
    return ((frame.astype(np.uint16) * (255 - a) + rgb * a + 127) // 255).astype(np.uint8)


def brackets(d: ImageDraw.ImageDraw, x0, y0, x1, y1, L, color, width):
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([cx, cy, cx + sx * L, cy], fill=color, width=width)
        d.line([cx, cy, cx, cy + sy * L], fill=color, width=width)


def cursor(d: ImageDraw.ImageDraw, x, y, k):
    pts = [(0, 0), (0, 26), (7, 20), (12, 31), (17, 29), (12, 18), (21, 18)]
    pts = [(x + px * k, y + py * k) for px, py in pts]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(10, 12, 20, 255))


def glitch(frame: np.ndarray, amount: float, seed: int) -> np.ndarray:
    """RGB split, sliced row offsets and noise lines; amount 0-1."""
    if amount <= 0:
        return frame
    rng = np.random.default_rng(seed)
    out = frame.copy()
    dx = int(24 * amount)
    out[..., 0] = np.roll(frame[..., 0], dx, axis=1)
    out[..., 2] = np.roll(frame[..., 2], -dx, axis=1)
    h = frame.shape[0]
    for _ in range(int(10 * amount) + 1):
        y = int(rng.integers(0, h - 8))
        hh = int(rng.integers(4, max(6, int(80 * amount))))
        out[y:y + hh] = np.roll(out[y:y + hh], int(rng.integers(-120, 120) * amount), axis=1)
    for _ in range(int(6 * amount)):
        y = int(rng.integers(0, h - 2))
        out[y:y + 2] = 255 - out[y:y + 2] // 2
    return out


class Feed:
    """The control-room overlay over the running timelapse."""

    def __init__(self, kit: Kit, start, end, starts, nights, rows_by_ts, caps, moments):
        self.kit, self.start, self.end, self.starts = kit, start, end, starts
        self.rows = rows_by_ts           # [(ts, line)] of sightings, for the live log
        self.row_ts = [t for t, _ in rows_by_ts]
        self.caps = caps
        w, h, k = kit.w, kit.h, kit.k
        self.bar_h = round(92 * k)
        self.bar_y = h - self.bar_h
        self.px0, self.px1 = round(40 * k), w - round(40 * k)
        self.py0, self.py1 = self.bar_y + round(14 * k), self.bar_y + round(56 * k)
        base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(base)
        d.rectangle([0, self.bar_y, w, h], fill=(4, 10, 16, 255))
        d.line([0, self.bar_y, w, self.bar_y], fill=(*CYAN, 255), width=1)
        lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        m = round(18 * k)
        brackets(ld, m, m, w - m, self.bar_y - m, round(46 * k), (*CYAN, 170), max(2, round(2 * k)))
        for a, b in nights:
            ld.rectangle([self.x(a), self.py0, self.x(b), self.py1], fill=(40, 60, 140, 110))
        bins = max(1, math.ceil((end - start) / 3600))
        counts = np.bincount([min(bins - 1, int((t - start) // 3600)) for t in starts], minlength=bins)
        top = max(1, int(np.percentile(counts, 99)) if counts.any() else 1)
        for i, c in enumerate(counts):  # bar chart, one column per hour
            y = self.py1 - min(1.0, c / top) * (self.py1 - self.py0)
            ld.rectangle([self.x(start + i * 3600), y, self.x(start + (i + 1) * 3600) - 1, self.py1],
                         fill=(*CYAN, 150))
        for mo in moments:
            x = self.x(mo.ts)
            ld.polygon([(x, self.py0 - 2 * k), (x - 5 * k, self.py0 - 11 * k), (x + 5 * k, self.py0 - 11 * k)],
                       fill=(*AMBER, 255))
        day = datetime.fromtimestamp(start).replace(hour=0, minute=0, second=0, microsecond=0)
        while day.timestamp() < end:
            if day.timestamp() > start:
                x = self.x(day.timestamp())
                ld.line([x, self.py0 - 4 * k, x, self.py1 + 4 * k], fill=(255, 255, 255, 90))
            noon = day.timestamp() + 12 * 3600
            if start < noon < end:
                ld.text((self.x(noon), self.py1 + 17 * k), f"{day:%a}".upper() + f" {day:%m-%d}",
                        font=kit.mono_s, fill=(*CYAN, 220), anchor="mm")
            day = datetime.fromtimestamp(day.timestamp() + 26 * 3600).replace(hour=0, minute=0)
        base.alpha_composite(lay)
        self.base = base
        dim = np.asarray(base).copy()
        dim[self.py0 - round(12 * k):self.py1 + round(5 * k), :, :3] = \
            (dim[self.py0 - round(12 * k):self.py1 + round(5 * k), :, :3] * 0.4 + np.array((4, 10, 16)) * 0.6
             ).astype(np.uint8)
        self.dim = Image.fromarray(dim)
        self._log_key, self._log_img = None, None

    def x(self, ts):
        return self.px0 + (ts - self.start) / max(1.0, self.end - self.start) * (self.px1 - self.px0)

    def frame(self, ts: float, t_out: float, t_src: float) -> Image.Image:
        kit, k, w = self.kit, self.kit.k, self.kit.w
        xp = int(self.x(ts))
        img = self.base.copy()
        img.paste(self.dim.crop((xp, 0, w, kit.h)), (xp, 0))
        d = ImageDraw.Draw(img)
        dt = datetime.fromtimestamp(ts)
        m = round(46 * k)
        d.text((m, round(38 * k)), "CAM-01 // CAMPI STREET FEED // LIVE", font=kit.mono_s, fill=(*CYAN, 230))
        d.text((m, round(60 * k)), f"{dt:%a %Y-%m-%d}".upper(), font=kit.mono_l, fill=(*WHITE, 255))
        d.text((m - 3, round(90 * k)), f"{dt:%H:%M:%S}", font=kit.clock, fill=(*WHITE, 255))
        el = ts - self.start
        d.text((m, round(172 * k)), f"T+{int(el // 86400):02d}d {int(el % 86400 // 3600):02d}h "
                                    f"{int(el % 3600 // 60):02d}m", font=kit.mono, fill=(*GREEN, 230))
        # REC and counters
        rx = w - m
        if int(t_out * 2) % 2 == 0:
            d.ellipse([rx - round(110 * k), round(40 * k), rx - round(94 * k), round(56 * k)], fill=(*RED, 255))
        d.text((rx, round(36 * k)), "REC", font=kit.mono_l, fill=(*RED, 255), anchor="ra")
        n = bisect.bisect_right(self.starts, ts)
        rate = n - bisect.bisect_right(self.starts, ts - 3600)
        d.text((rx, round(78 * k)), "VEHICLES LOGGED", font=kit.mono_s, fill=(*CYAN, 230), anchor="ra")
        d.text((rx, round(98 * k)), f"{n:07,d}", font=kit.count, fill=(*WHITE, 255), anchor="ra")
        d.text((rx, round(152 * k)), f"RATE {rate:3d}/HR", font=kit.mono, fill=(*GREEN, 230), anchor="ra")
        # live detection log (refreshed 10x a second; at this speed it is a blur of plates, on purpose)
        key = int(t_out * 10)
        if key != self._log_key:
            self._log_key = key
            self._log_img = self.log_panel(ts)
        img.alpha_composite(self._log_img, dest=(w - self._log_img.width - m, round(200 * k)))
        # playhead
        d.line([xp, self.py0 - round(12 * k), xp, self.py1 + round(3 * k)], fill=(*WHITE, 255),
               width=max(2, round(2 * k)))
        # event lines, terminal style
        y = self.bar_y - round(24 * k)
        for c in self.caps:
            if c["t"] <= t_src <= c["t"] + 2.2:
                a = min(1.0, (t_src - c["t"]) / 0.15, (c["t"] + 2.2 - t_src) / 0.25)
                shown = c["line"][:max(1, int((t_src - c["t"]) * 70))]
                bw = d.textlength(c["line"], font=kit.mono_l) + round(28 * k)
                y -= round(46 * k)
                box = Image.new("RGBA", (int(bw), round(40 * k)), (4, 10, 16, int(200 * a)))
                bd = ImageDraw.Draw(box)
                bd.rectangle([0, 0, round(5 * k), box.height], fill=(*c["color"], int(255 * a)))
                bd.text((round(16 * k), round(6 * k)), shown + ("_" if int(t_out * 4) % 2 else ""),
                        font=kit.mono_l, fill=(*c["color"], int(255 * a)))
                img.alpha_composite(box, dest=(m, y))
        return img

    def log_panel(self, ts: float) -> Image.Image:
        kit, k = self.kit, self.kit.k
        i = bisect.bisect_right(self.row_ts, ts)
        lines = [line for _, line in self.rows[max(0, i - 9):i]][::-1]
        pw, lh = round(470 * k), round(25 * k)
        img = Image.new("RGBA", (pw, round(40 * k) + lh * 9), (4, 10, 16, 150))
        d = ImageDraw.Draw(img)
        d.text((round(12 * k), round(8 * k)), "LIVE DETECTION LOG", font=kit.mono_s, fill=(*CYAN, 255))
        d.line([0, round(32 * k), pw, round(32 * k)], fill=(*CYAN, 120))
        for j, line in enumerate(lines):
            d.text((round(12 * k), round(38 * k) + j * lh), line, font=kit.mono_s,
                   fill=(*(GREEN if j == 0 else WHITE), 255 - j * 22))
        return img


class Freeze:
    """One moment's FREEZE_S seconds: everything is a function of t, so frames can be drawn in any order."""

    def __init__(self, kit: Kit, m: Moment, n: int):
        self.kit, self.m, self.n = kit, m, n
        img = cv2.imread(str(m.image), cv2.IMREAD_COLOR)
        self.img = img
        ih, iw = img.shape[:2]
        bx, by, bw, bh = m.box or (0.4, 0.4, 0.2, 0.2)
        self.box_px = (bx * iw, by * ih, bw * iw, bh * ih)
        # the PiP's source region: the box grown to 16:9 and at least a fifth of the frame wide
        cx, cy = (bx + bw / 2) * iw, (by + bh / 2) * ih
        rw = min(iw, max(bw * iw * 1.5, bh * ih * 1.5 * 16 / 9, iw * 0.2))  # never wider or taller than the image
        rh = rw * 9 / 16
        if rh > ih:
            rh, rw = ih, ih * 16 / 9
        x0 = int(min(max(0, cx - rw / 2), iw - rw))
        y0 = int(min(max(0, cy - rh / 2), ih - rh))
        self.region = img[y0:y0 + int(rh), x0:x0 + int(rw)]
        k = kit.k
        self.pip_w, self.pip_h = round(600 * k), round(338 * k)
        sharp = cv2.resize(self.region, (self.pip_w, self.pip_h), interpolation=cv2.INTER_CUBIC)
        blur = cv2.GaussianBlur(sharp, (0, 0), 2.2)
        self.sharp = cv2.addWeighted(sharp, 1.8, blur, -0.8, 0)  # "enhanced": upscaled and unsharp-masked
        self.clip = self.load_clip(m.clip, cy / ih) if m.clip else []
        self.before = None
        if m.compare:
            prev = cv2.imread(str(m.compare), cv2.IMREAD_COLOR)
            if prev is not None and prev.shape == img.shape:
                pr = cv2.resize(prev[y0:y0 + int(rh), x0:x0 + int(rw)], (self.pip_w, self.pip_h),
                                interpolation=cv2.INTER_CUBIC)
                self.before = cv2.addWeighted(pr, 1.8, cv2.GaussianBlur(pr, (0, 0), 2.2), -0.8, 0)

    def load_clip(self, p: Path, cy: float) -> list[np.ndarray]:
        """The sighting clip as PiP-sized frames: a 16:9 band of the full width around the car's height."""
        cap = cv2.VideoCapture(str(p))
        out, score = [], []
        ref = cv2.resize(cv2.cvtColor(self.img, cv2.COLOR_BGR2GRAY), (96, 72), interpolation=cv2.INTER_AREA)
        while len(out) < 400:
            ok, f = cap.read()
            if not ok:
                break
            h, w = f.shape[:2]
            bh = int(w * 9 / 16)
            y0 = int(min(max(0, cy * h - bh / 2), max(0, h - bh)))
            band = f[y0:y0 + bh]
            out.append(cv2.resize(band, (self.pip_w, self.pip_h), interpolation=cv2.INTER_AREA))
            g = cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (96, 72), interpolation=cv2.INTER_AREA)
            score.append(float(np.mean((g.astype(np.float32) - ref) ** 2)))
        cap.release()
        if not out:
            return []
        # the playback window: around the clip frame that looks most like the sighting frame (the car in view)
        best = int(np.argmin(score))
        n = int((FREEZE_S - 0.25 - PLAY_AT) * 30)
        a = max(0, min(best - n * 2 // 3, len(out) - n))
        return out[a:a + n]

    def view(self, t: float):
        """The zoomed main view: (x0, y0, scale) in image pixels; drifts in toward the subject."""
        ih, iw = self.img.shape[:2]
        e = ease(span(t, 0.6, FREEZE_S - 0.2))
        s = 1 + 0.32 * e
        bx, by, bw, bh = self.box_px
        cx = iw / 2 + (bx + bw / 2 - iw / 2) * e
        cy = ih / 2 + (by + bh / 2 - ih / 2) * e
        vw, vh = iw / s, ih / s
        x0 = min(max(0, cx - vw / 2), iw - vw)
        y0 = min(max(0, cy - vh / 2), ih - vh)
        return x0, y0, s

    def frame(self, t: float) -> np.ndarray:
        kit, k, w, h, m = self.kit, self.kit.k, self.kit.w, self.kit.h, self.m
        ih, iw = self.img.shape[:2]
        x0, y0, s = self.view(t)
        vw, vh = iw / s, ih / s
        M = np.float32([[w / vw, 0, -x0 * w / vw], [0, h / vh, -y0 * h / vh]])
        base = cv2.warpAffine(self.img, M, (w, h), flags=cv2.INTER_LINEAR)
        base = kit.apply_grade(base)
        sc = w / vw

        def to_out(x, y):
            return (x - x0) * sc, (y - y0) * sc

        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        # grid and scan line
        ga = int(40 * span(t, 0.2, 0.6))
        if ga:
            step = round(60 * k)
            for gx in range(0, w, step):
                d.line([gx, 0, gx, h], fill=(*CYAN, ga))
            for gy in range(0, h, step):
                d.line([0, gy, w, gy], fill=(*CYAN, ga))
        if 0.2 <= t <= 1.1:
            sy = h * span(t, 0.2, 1.1)
            for i in range(24):
                d.line([0, sy - i * 3, w, sy - i * 3], fill=(*CYAN, int(150 * (1 - i / 24))))
        # target: brackets converge on the subject and lock
        bx, by, bw, bh = self.box_px
        tx0, ty0 = to_out(bx - bw * 0.08, by - bh * 0.12)
        tx1, ty1 = to_out(bx + bw * 1.08, by + bh * 1.12)
        e = ease(span(t, 0.65, 1.25))
        mg = round(60 * k)
        rx0, ry0 = mg + (tx0 - mg) * e, mg + (ty0 - mg) * e
        rx1, ry1 = (w - mg) + (tx1 - (w - mg)) * e, (h - mg) + (ty1 - (h - mg)) * e
        locked = t >= 1.25
        if t >= 0.65:
            blink = locked and t < 1.7 and int((t - 1.25) * 10) % 2 == 0
            col = (*(RED if blink else (AMBER if not locked else CYAN)), 255)
            brackets(d, rx0, ry0, rx1, ry1, round(28 * k), col, max(3, round(3 * k)))
            if locked:
                cxo, cyo = (rx0 + rx1) / 2, (ry0 + ry1) / 2
                for (a, b) in (((0, cyo), (rx0, cyo)), ((rx1, cyo), (w, cyo)), ((cxo, 0), (cxo, ry0)),
                               ((cxo, ry1), (cxo, h))):
                    d.line([a, b], fill=(*CYAN, 90), width=1)
                d.text((rx0, ry0 - round(30 * k)), f"TARGET {self.n:02d} // LOCKED", font=kit.mono,
                       fill=(*(RED if blink else CYAN), 255))
                d.text((rx0, ry1 + round(8 * k)),
                       f"X{int(bx):04d} Y{int(by):04d}  {int(bw)}x{int(bh)}PX", font=kit.mono_s, fill=(*CYAN, 230))
            else:
                d.text((rx0, ry0 - round(30 * k)), "ACQUIRING" + "." * (int(t * 8) % 4), font=kit.mono,
                       fill=(*AMBER, 255))
        # header: analysis mode
        m_ = round(46 * k)
        if int(t * 3) % 2 == 0:
            d.ellipse([m_, round(44 * k), m_ + round(16 * k), round(60 * k)], fill=(*RED, 255))
        d.text((m_ + round(28 * k), round(36 * k)), "ANALYSIS MODE // FEED PAUSED", font=kit.mono_l, fill=(*RED, 255))
        d.text((m_, round(74 * k)), f"FRAME {datetime.fromtimestamp(m.ts):%Y-%m-%d %H:%M:%S}  {m.stamp}",
               font=kit.mono_s, fill=(*CYAN, 230))
        # PiP: slides in, gets clicked, enhances, then plays the source clip
        pw, ph = self.pip_w, self.pip_h
        right = (bx + bw / 2) / iw < 0.5
        slide = int((1 - ease(span(t, 1.3, 1.55))) * (pw + 2 * m_))
        px = w - pw - m_ + slide if right else m_ - slide
        py = round(150 * k)
        cur = None
        if t >= 1.3:
            pip = self.pip_image(t)
            pim = Image.fromarray(cv2.cvtColor(pip, cv2.COLOR_BGR2RGB)).convert("RGBA")
            ov.alpha_composite(pim, dest=(px, py))
            d.rectangle([px - 2, py - 2, px + pw + 1, py + ph + 1], outline=(*CYAN, 255), width=2)
            brackets(d, px - 8, py - 8, px + pw + 7, py + ph + 7, round(22 * k), (*CYAN, 255), 2)
            phase = self.pip_phase(t)
            d.rectangle([px - 2, py - round(34 * k), px + pw + 1, py - 4], fill=(4, 10, 16, 230))
            d.text((px + round(10 * k), py - round(30 * k)), phase, font=kit.mono, fill=(*CYAN, 255))
            # the ENHANCE button the cursor clicks, then a progress bar
            by_ = py + ph + round(14 * k)
            if t < 3.25:
                pr = span(t, 2.0, 3.2)
                bw_ = round(220 * k)
                pressed = 1.95 <= t < 2.1
                d.rectangle([px, by_, px + bw_, by_ + round(42 * k)], outline=(*CYAN, 255), width=2,
                            fill=(*CYAN, 200) if pressed else (4, 10, 16, 220))
                d.text((px + bw_ / 2, by_ + round(21 * k)), "[ ENHANCE ]", font=kit.mono_l,
                       fill=(4, 10, 16, 255) if pressed else (*CYAN, 255), anchor="mm")
                if t >= 2.0:
                    bx_ = px + bw_ + round(16 * k)
                    bl = pw - bw_ - round(16 * k)
                    d.rectangle([bx_, by_ + round(10 * k), bx_ + bl, by_ + round(32 * k)], outline=(*CYAN, 255))
                    d.rectangle([bx_ + 3, by_ + round(13 * k), bx_ + 3 + (bl - 6) * pr, by_ + round(29 * k)],
                                fill=(*GREEN, 255))
                    d.text((bx_, by_ + round(38 * k)), f"RECONSTRUCTING {int(pr * 100):3d}%", font=kit.mono_s,
                           fill=(*GREEN, 255))
                cur = (px + bw_ * 0.6, by_ + round(26 * k))
            elif t < 3.5 and int(t * 12) % 2 == 0:
                d.text((px + pw / 2, py + ph / 2), "ENHANCED", font=kit.title_l, fill=(*GREEN, 255),
                       anchor="mm", stroke_width=3, stroke_fill=(4, 10, 16, 255))
        # dossier, typed out
        if t >= 1.4:
            dy = py + ph + round(112 * k)
            lines = [f"{m.title} {self.n:02d} // {m.tag}"] + m.lines
            chars = int((t - 1.4) * TYPE_CPS)
            lh = round(30 * k)
            dw = round(max(kit.title.getlength(lines[0]), max(kit.mono.getlength(x) for x in lines[1:])) + 28 * k)
            dx = w - m_ - dw + round(14 * k) if right else m_
            d.rectangle([dx - round(14 * k), dy - round(14 * k), dx + dw, dy + lh * len(lines) + round(12 * k)],
                        fill=(4, 10, 16, 200), outline=(*CYAN, 160))
            for i, line in enumerate(lines):
                if chars <= 0:
                    break
                shown = line[:chars]
                chars -= len(line) + 4
                f = kit.title if i == 0 else kit.mono
                col = AMBER if i == 0 else (GREEN if line.startswith(">") else WHITE)
                d.text((dx, dy + i * lh), shown + ("_" if chars < 0 and int(t * 6) % 2 else ""), font=f,
                       fill=(*col, 255))
        # the cursor: to the subject, click; then to the ENHANCE button, click
        if t < 0.65:
            c0, c1 = (w * 0.85, h * 0.9), ((tx0 + tx1) / 2, (ty0 + ty1) / 2)
            e = ease(span(t, 0.15, 0.6))
            cursor(d, c0[0] + (c1[0] - c0[0]) * e, c0[1] + (c1[1] - c0[1]) * e, k)
        elif t < 1.55:
            c = ((tx0 + tx1) / 2, (ty0 + ty1) / 2)
            if t < 0.95:
                r = (t - 0.65) * 300 * k
                d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=(*WHITE, int(255 * (1 - (t - 0.65) / 0.3))),
                          width=3)
            cursor(d, *c, k)
        elif cur and t < 2.4:
            c0 = ((tx0 + tx1) / 2, (ty0 + ty1) / 2)
            e = ease(span(t, 1.55, 1.95))
            cursor(d, c0[0] + (cur[0] - c0[0]) * e, c0[1] + (cur[1] - c0[1]) * e, k)
        out = blend(base, ov)
        if t < 0.22:
            out = glitch(out, 1 - t / 0.22, int(t * 1000))
            if t < 0.06:
                out = np.full_like(out, 255) // 2 + out // 2
        if t > FREEZE_S - 0.22:
            out = glitch(out, (t - (FREEZE_S - 0.22)) / 0.22, int(t * 1000))
        return out

    def pip_phase(self, t: float) -> str:
        if t < 2.0:
            return "SECTOR LOCK // AWAITING INPUT"
        if t < 3.25:
            return f"ENHANCE x{2 ** (1 + int(span(t, 2.0, 3.2) * 5))} // IMAGE RECONSTRUCTION"
        if self.clip and t >= PLAY_AT:
            return "SOURCE FOOTAGE // PLAYBACK 1x"
        if self.before is not None and t >= PLAY_AT:
            return f"DELTA // {self.m.compare_label} vs {self.m.now_label}"
        return "ENHANCED // 4x SUPER-RESOLUTION"

    def pip_image(self, t: float) -> np.ndarray:
        pw, ph = self.pip_w, self.pip_h
        if t < 3.2:
            steps = [40, 28, 18, 11, 6, 3]
            b = steps[min(len(steps) - 1, int(span(t, 2.0, 3.2) * len(steps)))] if t >= 2.0 else steps[0]
            small = cv2.resize(self.region, (max(2, pw // b), max(2, ph // b)), interpolation=cv2.INTER_AREA)
            img = cv2.resize(small, (pw, ph), interpolation=cv2.INTER_NEAREST)
        elif self.clip and t >= PLAY_AT:
            i = min(len(self.clip) - 1, int((t - PLAY_AT) * 30))
            img = self.clip[i]
        elif self.before is not None and t >= PLAY_AT:
            # wipe: the earlier view on the left of a sweeping line, this one on the right, and back
            u = (t - PLAY_AT) / (FREEZE_S - 0.3 - PLAY_AT)
            xw = int(pw * (0.5 + 0.45 * math.cos(math.pi * 2 * u)))
            img = self.sharp.copy()
            img[:, :xw] = self.before[:, :xw]
            img[:, max(0, xw - 2):xw + 2] = (255, 225, 70)
        else:
            img = self.sharp
        img = img.copy()
        img[::3] = (img[::3] * 0.82).astype(np.uint8)
        return img


# --- sound --------------------------------------------------------------------------------------------------------

def synth(total_s: float, freezes: list[float], typing: list[tuple[float, float]], out: Path) -> None:
    n = int(total_s * SR)
    t = np.arange(n) / SR
    # bed: a low drone with a slow pulse
    a = 0.05 * (np.sin(2 * np.pi * 55 * t) + 0.6 * np.sin(2 * np.pi * 110.5 * t)) * (0.75 + 0.25 * np.sin(2 * np.pi
                                                                                                        * 0.25 * t))
    rng = np.random.default_rng(7)

    def add(at, sig):
        i = int(at * SR)
        if 0 <= i < n:
            j = min(n, i + len(sig))
            a[i:j] += sig[:j - i]

    def tone(f, d, vol=0.25, f1=None):
        tt = np.arange(int(d * SR)) / SR
        ff = f if f1 is None else f + (f1 - f) * tt / d
        env = np.minimum(1, tt / 0.005) * np.exp(-tt / (d * 0.6))
        return vol * np.sin(2 * np.pi * np.cumsum(ff) / SR) * env

    def noise(d, vol=0.3, crush=6):
        s = rng.uniform(-1, 1, int(d * SR))
        s = np.repeat(s[::crush], crush)[:int(d * SR)]
        return vol * s * np.linspace(1, 0, len(s))

    for f0 in freezes:
        add(f0, noise(0.24, 0.35))
        add(f0 + 0.05, tone(180, 0.5, 0.25, 60))                    # the feed dropping
        a[int((f0 + 0.3) * SR):int((f0 + FREEZE_S - 0.3) * SR)] += \
            0.03 * np.sin(2 * np.pi * 880 * t[int((f0 + 0.3) * SR):int((f0 + FREEZE_S - 0.3) * SR)]) * \
            (np.sin(2 * np.pi * 3 * t[int((f0 + 0.3) * SR):int((f0 + FREEZE_S - 0.3) * SR)]) > 0.6)
        add(f0 + 0.65, tone(1600, 0.06, 0.2))                       # click
        for i in range(5):
            add(f0 + 0.7 + i * 0.1, tone(900 + 120 * i, 0.05, 0.12))  # acquiring
        add(f0 + 1.25, tone(1320, 0.12, 0.3))
        add(f0 + 1.4, tone(1760, 0.18, 0.3))                        # lock
        add(f0 + 1.3, noise(0.25, 0.12, 2))                         # PiP whoosh
        add(f0 + 1.95, tone(1600, 0.06, 0.2))                       # click ENHANCE
        for i in range(6):
            add(f0 + 2.0 + i * 0.2, tone(500 * 1.25 ** i, 0.14, 0.22))  # enhance steps
        add(f0 + 3.22, tone(880, 0.35, 0.3, 1760))                  # done
        add(f0 + FREEZE_S - 0.24, noise(0.24, 0.35))
    for t0, t1 in typing:
        for tt in np.arange(t0, t1, 1 / 26):
            add(tt + rng.uniform(0, 0.01), noise(0.012, 0.18, 1))
    a = np.tanh(a * 1.4) * 0.8
    pcm = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


# --- the pass -------------------------------------------------------------------------------------------------------

def run(cfg, src: Path, job: dict | None, ai: bool, preview: float | None, out: str | None) -> dict:
    t_start = time.time()
    idx = render_index.read(cfg, src.name)
    frame_ts, interp = idx["frame_ts"], int(idx.get("interp_factor") or 1)
    probe = ffprobe(cfg, src)
    vs = next(s for s in probe["streams"] if s.get("codec_type") == "video")
    w, h = int(vs["width"]), int(vs["height"])
    num, den = (int(x) for x in vs["avg_frame_rate"].split("/"))
    fps = num / den
    duration = float(probe["format"]["duration"])
    start, end = frame_ts[0], frame_ts[-1]
    sd = state_dir(cfg)

    starts, by, known = traffic(cfg, start, end)
    rows = captured(cfg, start, end)
    nights = night_spans(rows)
    notes = ai_notes(cfg, frame_ts, sd / f"{src.stem}.ai.json") if ai else []
    frame_files = [(f.ts, f.path) for f in rows]
    fts = [t for t, _ in frame_files]

    def frames_by_ts(ts):
        i = min(len(fts) - 1, bisect.bisect_left(fts, ts))
        return frame_files[i][1]

    moments = pick_moments(cfg, start, end, by, known, notes, frames_by_ts, sd / f"{src.stem}.zooms.json")
    # each freeze goes before the source frame showing its moment; too-close ones are dropped
    placed = []
    for mo in sorted(moments, key=lambda m: {"mycar": 0, "ai": 1}.get(m.kind, 2)):
        mo.k = bisect.bisect_left(frame_ts, mo.ts) * interp
        if mo.k / fps < duration - 1 and all(abs(mo.k - o.k) / fps >= MIN_GAP_S for o in placed):
            placed.append(mo)
    moments = sorted(placed, key=lambda m: m.k)
    log.info("forensic: %d moments: %s", len(moments),
             "; ".join(f"{m.k / fps:.1f}s {m.kind} {m.tag}" for m in moments))

    # terminal-style event lines for what isn't a freeze
    events = [e for e in build_events(frame_ts, rows, starts, by, known, nights) if e["kind"] in
              ("sun", "busy", "wrap", "offline")]
    caps = schedule(events, frame_ts, fps, interp, duration)
    colors = {"sun": AMBER, "busy": CYAN, "wrap": GREEN, "offline": RED}
    for c in caps:
        tm = datetime.fromtimestamp(c["ts"]).strftime("%H:%M")
        c["line"] = f"[{tm}] {c['label'].split(' · ')[0]} // {c['text'].upper()}"[:64]
        c["color"] = colors[c["kind"]]
    sight_lines = []
    for r in sighting_rows(cfg, start, end):
        arrow = {"LR": "->", "RL": "<-"}.get(r.get("direction") or "", "  ")
        name = (r.get("label") or "?").upper()[:20]
        sight_lines.append((r["ts"], f"{datetime.fromtimestamp(r['ts']):%H:%M:%S} {name:20} {arrow} "
                                     f"{100 * (r.get('confidence') or 0):3.0f}%"))
    sight_lines.sort()

    kit = Kit(w, h)
    feed = Feed(kit, start, end, starts, nights, sight_lines, caps, moments)

    if out:
        dst = Path(out)
    elif preview:
        dst = cfg.paths.work / f"{src.stem}_forensic_preview.mp4"
    elif job:
        new = {**cj.new_job(job["start_ts"], job["end_ts"], job.get("speed"), job.get("seconds")),
               "annotated_of": job["id"], "style": "forensic"}
        dst = cfg.paths.custom / cj.file_name(new)
    else:
        dst = src.with_name(f"{src.stem}_forensic.mp4")
    silent = cfg.paths.work / f"{dst.stem}.video.mp4"
    wav = cfg.paths.work / f"{dst.stem}.wav"

    src_frames = round(min(duration, preview or duration) * fps)
    fz_n = round(FREEZE_S * fps)
    dec = subprocess.Popen([cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src),
                            "-frames:v", str(src_frames), "-vf", "scale=in_color_matrix=bt709:in_range=tv,format=bgr24",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE,
                           creationflags=NO_WINDOW)
    encoder = "nvenc" if cfg.output.encoder in ("auto", "nvenc") and nvenc_available(cfg) else "x264"
    enc = subprocess.Popen(ffmpeg_cmd(cfg, encoder, w, h, round(fps), silent, deflicker=False), stdin=subprocess.PIPE,
                           creationflags=NO_WINDOW)
    size = w * h * 3
    out_i, out_ts, freezes_at, typing = 0, [], [], []
    pending = list(moments)
    k = 0
    try:
        while k < src_frames:
            while pending and pending[0].k <= k:
                mo = pending.pop(0)
                fz = Freeze(kit, mo, len(freezes_at) + 1)
                t0 = out_i / fps
                freezes_at.append(t0)
                n_chars = sum(len(x) + 4 for x in [mo.title] + mo.lines)
                typing.append((t0 + 1.4, t0 + min(FREEZE_S - 0.3, 1.4 + n_chars / TYPE_CPS)))
                for j in range(fz_n):
                    enc.stdin.write(fz.frame(j / fps).tobytes())
                    if j % interp == 0:
                        out_ts.append(mo.ts)
                    out_i += 1
                log.info("freeze %d (%s %s) at %.1f s", len(freezes_at), mo.kind, mo.tag, t0)
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            ts = frame_ts[min(len(frame_ts) - 1, k // interp)]
            frame = kit.apply_grade(frame)
            enc.stdin.write(blend(frame, feed.frame(ts, out_i / fps, k / fps)).tobytes())
            if k % interp == 0:
                out_ts.append(ts)
            out_i += 1
            k += 1
            if k % 600 == 0:
                log.info("forensic %s: %d/%d source frames", dst.name, k, src_frames)
    finally:
        enc.stdin.close()
        rc = enc.wait()
        dec.kill()
    if rc != 0:
        raise RuntimeError(f"encoder exited {rc}")
    total_s = out_i / fps
    synth(total_s, freezes_at, typing, wav)
    tmp = dst.with_name(dst.stem + ".part.mp4")
    r = subprocess.run([cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent), "-i", str(wav),
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
                        str(tmp)],
                       creationflags=NO_WINDOW)
    silent.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("muxing the sound failed")
    tmp.replace(dst)
    res = {"output": str(dst), "frames": out_i, "duration_s": round(total_s, 1), "moments": len(freezes_at),
           "elapsed_s": round(time.time() - t_start, 1)}
    if job and not preview and not out:
        render_index.write(cfg, dst, start, end, out_ts)
        cj.write_job(cfg, {**new, "status": "ok", "progress": 1.0, "output": dst.name, "duration_s": total_s,
                           "frames_used": job.get("frames_used"), "frames_available": job.get("frames_available"),
                           "actual_speed": round((end - start) / total_s, 1), "finished": time.time()})
        res["job"] = new["id"]
    log.info("forensic %s -> %s (%.0f s video, %d freezes, %.0f s)", src.name, dst.name, total_s, len(freezes_at),
             res["elapsed_s"])
    return res
