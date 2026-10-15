"""`campi site`: the water tower build as a video game (venv-sightings: numpy, cv2, PIL, google-genai).

1. The intro (intro.py): a 3D build of what's going up, "THIS IS WHAT'S BEING BUILT", the build order and specs.
2. The live site feed: only the stretches where something happened at the tower (activity.py: crews, vehicles,
   the concrete pump's boom over the top), stabilized on the reference frame and cut out of the scene. Each stretch
   gets an objective panel saying what's going on (Gemini, from three frames of it; cached), held long enough to read:
   max(4 s, 1.5 s + 0.35 s a word). Nights, idle hours and Sundays are skipped; a card says how much time was skipped.
   The HUD counts the concrete pours seen and charts the pedestal's height (the day's skyline over it).
3. An ending: the first day against the latest, wiped, and what comes next.

The result is a custom timelapse job (state\\custom\\, style "site"), so it is on the Timelapse tab.
"""
from __future__ import annotations

import bisect
import logging
import math
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import activity as ac
from . import custom_jobs as cj
from . import intro
from . import render_index
from . import site_ai
from .annotate import captured, state_dir
from .config import NO_WINDOW, read_json, write_json
from .focus import daylight, view
from .forensic import blend, ease, span, synth
from .render import ffmpeg_cmd, nvenc_available

log = logging.getLogger("site")

BOX = (0, 330, 720, 870)       # the tower and its yard, camera pixels at the reference (4:3)
TOWER_COLS = (0.14, 0.33)      # the pedestal's columns in the box (its height is measured there)
TOWER_BASE = 0.98              # its foot, box-relative
LIFT_PX = 21.2                 # one jump-form lift, camera pixels (the lift lines on the pedestal, measured 10-14)
FPS = 30
MAX_FOOTAGE_S = 90.0
MIN_SCORE = 500                # weaker segments are a car parking, not work
POUR_SKY = 5                   # a segment with this many boom samples is a concrete pour
CAPTION_GAP_S = 0.8            # between one caption leaving and the next arriving
END_S = 6.0
GAME = intro.GAME
CYAN, GOLD, GREEN, WHITE, DIM, PANEL = intro.CYAN, intro.GOLD, intro.GREEN, intro.WHITE, intro.DIM, intro.PANEL


def read_s(words: int) -> float:
    return max(4.0, 1.5 + 0.35 * words)


@dataclass
class Chapter:
    """Half a day's work: its active segments (morning or afternoon), shown as one stretch with one caption."""
    segs: list[ac.Segment]
    pours: list[float]          # start of each concrete pour in it
    tag: str = ""
    title: str = ""
    detail: str = ""
    dur: float = 0.0            # output seconds
    frames: list | None = None  # (frame, M)

    @property
    def pour(self) -> bool:
        return bool(self.pours)

    @property
    def start(self) -> float:
        return self.segs[0].start

    @property
    def end(self) -> float:
        return self.segs[-1].end

    @property
    def samples(self) -> list[ac.Sample]:
        return [s for g in self.segs for s in g.samples]

    @property
    def score(self) -> float:
        return sum(g.score for g in self.segs)

    @property
    def bbox(self):
        bs = [g.bbox for g in self.segs if g.bbox]
        return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]             if bs else None

    @property
    def words(self) -> int:
        return len((self.title + " " + self.detail).split())


# --- what happened: Claude, else Gemini ---------------------------------------------------------------------------

CAPTION_PROMPT = """You write the on-screen objective text for a video-game-style timelapse of a real construction
site, seen by a fixed camera across the road: a 0.5 MG composite elevated water tank. Its concrete pedestal is poured
in jump-form lifts; later a steel bowl is built at ground level and jacked up it. Known facts:
{facts}

The video shows {n} chapters in order; each has two stabilized close-ups (its busiest moment, then its end). The tower
is the concrete cylinder on the left. What the activity detector and the height measurements say about each:
{chapters}

Write one caption per chapter, in order, so that together they tell the story of the week: the first pour, the
pedestal climbing past the tree line, the crews resetting the forms between pours, the latest lift. Pours look alike
from across the road, so make each caption about something different: what is new in that chapter (the height
reached, a pour on a weekend, two pours in one day, the crew on top, equipment on the ground, the forms being raised).
Never repeat a title or reuse its wording. A chapter where the detector saw the concrete pump is a pour: tag it
CONCRETE POUR and vary its title and detail instead; never call a chapter without the pump a pour, and don't invent
inspections or deliveries that can't be seen. For each chapter return:
- tag: 1-2 words in capitals naming the activity (e.g. CONCRETE POUR, FORMWORK, DELIVERY, CREW AT WORK, CLEANUP),
- title: at most 6 words, sentence case,
- detail: at most 10 words, what is going on and why it matters for the build.
Only describe what can be seen or what the notes say; if unsure, keep it general. No clock times."""


def caption_lines(chapters: list[Chapter], context: list[str]) -> list[str]:
    """What the detector and the measurements say about each chapter, one line each (for the caption prompt)."""
    lines = []
    for i, (ch, ctx) in enumerate(zip(chapters, context)):
        when = f"{datetime.fromtimestamp(ch.start):%A}"
        sig = []
        if ch.pour:
            sig.append(f"the concrete pump's boom over the tower ({len(ch.pours)} "
                       f"pour{'s' if len(ch.pours) > 1 else ''})")
        else:
            sig.append("no concrete pump")
        n_ground = sum(s.ground >= ac.GROUND_MIN for s in ch.samples)
        if n_ground:
            sig.append(f"movement on the ground or on the tower in {n_ground} of {len(ch.samples)} checks")
        lines.append(f"{i + 1}. {when} {'afternoon' if datetime.fromtimestamp(ch.start).hour >= 12 else 'morning'}"
                     f": {'; '.join(sig)}. {ctx}")
    return lines


def captions(cfg, chapters: list[Chapter], imgs: list[list[np.ndarray]], ai: bool, cache: Path,
             context: list[str]) -> None:
    """One call for the whole sequence (it sees every chapter, so the captions build a story instead of
    repeating): Claude (site_ai), else Gemini; cached by the chapters' spans."""
    key = "|".join(f"{c.start:.0f}-{c.end:.0f}" for c in chapters)
    have = read_json(cache) if cache.is_file() else None
    caps = have["captions"] if isinstance(have, dict) and have.get("key") == key else None
    prompt = CAPTION_PROMPT.format(facts=intro.facts_text(), n=len(chapters),
                                   chapters="\n".join(caption_lines(chapters, context)))
    if caps is None and ai and chapters:
        caps = site_ai.captions(cfg, prompt, imgs, len(chapters))
        if caps:
            write_json(cache, {"key": key, "captions": caps, "by": site_ai.MODEL})
            log.info("claude captioned %d chapters", len(caps))
    if caps is None and ai and chapters:
        from google import genai
        from google.genai import types
        from pydantic import BaseModel

        from .sightings_cloud import THINKING

        class Cap(BaseModel):
            tag: str
            title: str
            detail: str

        parts = []
        for i, ims in enumerate(imgs):
            parts.append(f"Chapter {i + 1}:")
            parts += [types.Part.from_bytes(data=cv2.imencode(".jpg", cv2.resize(im, (768, 576)),
                                                              [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes(),
                                            mime_type="image/jpeg") for im in ims[1:]]
        client = genai.Client(api_key=Path(cfg.sightings.cloud.api_key_file).read_text(encoding="utf-8").strip(),
                              http_options=types.HttpOptions(timeout=240_000))
        for think in THINKING:
            try:
                resp = client.models.generate_content(
                    model=cfg.sightings.cloud.model, contents=[*parts, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", response_schema=list[Cap], temperature=0.7,
                        thinking_config=types.ThinkingConfig(**think) if think else None))
            except Exception as e:  # noqa: BLE001 - try the next thinking setting, else the generic captions
                if "think" in str(e).lower():
                    continue
                log.warning("captions failed: %s", e)
                break
            got = [c.model_dump() for c in resp.parsed] if resp.parsed else []
            if len(got) == len(chapters):
                caps = got
                write_json(cache, {"key": key, "captions": caps})
                log.info("gemini captioned %d chapters", len(caps))
            else:
                log.warning("gemini returned %d captions for %d chapters", len(got), len(chapters))
            break
    for i, ch in enumerate(chapters):
        d = caps[i] if caps else (
            {"tag": "CONCRETE POUR", "title": "Pump truck pours the next lift",
             "detail": "Concrete fills the forms on top, raising the pedestal."} if ch.pour else
            {"tag": "CREW AT WORK", "title": "Crew busy around the pedestal",
             "detail": "Workers and equipment moving around the tower."})
        ch.tag = str(d["tag"]).upper()[:24]
        ch.title = str(d["title"])[:48]
        ch.detail = str(d["detail"])[:110]


# --- the HUD ------------------------------------------------------------------------------------------------------

def wrap(d, text: str, font, width: float) -> list[str]:
    out, cur = [], ""
    for w_ in text.split():
        t = (cur + " " + w_).strip()
        if d.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            out.append(cur)
            cur = w_
    return out + ([cur] if cur else [])


class FeedHUD:
    def __init__(self, w, h, first_day, days_top: dict, pours_by: list[float]):
        self.w, self.h = w, h
        k = self.k = h / 1080
        self.f_tag = ImageFont.truetype(GAME, round(24 * k))
        self.f_clock = ImageFont.truetype(GAME, round(54 * k))
        self.f_small = ImageFont.truetype(GAME, round(21 * k))
        self.f_title = ImageFont.truetype(GAME, round(46 * k))
        self.f_detail = ImageFont.truetype(GAME, round(30 * k))
        self.f_big = ImageFont.truetype(GAME, round(84 * k))
        self.f_num = ImageFont.truetype(GAME, round(40 * k))
        self.first_day = first_day
        self.days_top = days_top            # date -> structure top (box-relative)
        self.pours = pours_by               # start ts of each pour, in order
        tops = [v for v in days_top.values() if v is not None]
        self.h0 = TOWER_BASE - tops[0] if tops else None
        self.hmax = TOWER_BASE - min(tops) if tops else None

    def panel(self, d, box, a, edge=CYAN):
        intro.panel(d, box, a, self.k, edge)

    def frame(self, ts: float, ch: Chapter | None, t_ch: float, t_out: float, skip: str | None,
              t_pour: float | None = None) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        k, w, h = self.k, self.w, self.h
        dt = datetime.fromtimestamp(ts)
        # top left: the feed and the clock
        self.panel(d, (round(24 * k), round(22 * k), round(430 * k), round(150 * k)), 1.0)
        if int(t_out * 2) % 2 == 0:
            d.ellipse([round(44 * k), round(42 * k), round(60 * k), round(58 * k)], fill=(255, 64, 88, 255))
        d.text((round(70 * k), round(38 * k)), "LIVE SITE FEED", font=self.f_tag, fill=(*CYAN, 255))
        day_n = (dt.date() - self.first_day).days + 1
        d.text((round(320 * k), round(38 * k)), f"DAY {day_n:02d}", font=self.f_tag, fill=(*GOLD, 255))
        d.text((round(44 * k), round(68 * k)), dt.strftime("%I:%M %p").lstrip("0"), font=self.f_clock,
               fill=(*WHITE, 255))
        d.text((round(410 * k), round(84 * k)), dt.strftime("%a %b %d").upper(), font=self.f_small,
               fill=(*DIM, 255), anchor="ra")
        # top right: pours and the pedestal's height
        x1 = w - round(24 * k)
        x0 = x1 - round(400 * k)
        self.panel(d, (x0, round(22 * k), x1, round(190 * k)), 1.0)
        n_p = bisect.bisect_right(self.pours, ts)
        d.text((x0 + round(20 * k), round(36 * k)), "CONCRETE POURS SEEN", font=self.f_small, fill=(*CYAN, 255))
        d.text((x1 - round(20 * k), round(28 * k)), str(n_p), font=self.f_num, fill=(*WHITE, 255), anchor="ra")
        if self.h0:
            top = self.top_at(dt.date())
            hh = TOWER_BASE - top
            d.text((x0 + round(20 * k), round(92 * k)), "PEDESTAL HEIGHT", font=self.f_small, fill=(*CYAN, 255))
            d.text((x1 - round(20 * k), round(86 * k)), f"{hh / self.h0 - 1:+.0%}", font=self.f_tag,
                   fill=(*GREEN, 255), anchor="ra")
            bx0, by0, bx1 = x0 + round(20 * k), round(128 * k), x1 - round(20 * k)
            d.rectangle([bx0, by0, bx1, by0 + round(18 * k)], outline=(*GREEN, 255), width=2)
            d.rectangle([bx0 + 3, by0 + 3, bx0 + 3 + (bx1 - bx0 - 6) * hh / self.hmax, by0 + round(18 * k) - 3],
                        fill=(*GREEN, 255))
            d.text((x0 + round(20 * k), round(156 * k)), "VS. DAY 01 · MEASURED EACH MIDDAY", font=self.f_small,
                   fill=(*DIM, 255))
        if ch is not None:
            self.objective(d, ch, t_ch)
            self.activity_box(d, ch, t_ch)
            if t_pour is not None and t_pour < 3.0:
                self.toast(d, "+1 CONCRETE POUR", t_pour)
        if skip:
            self.skip_card(d, skip, t_ch)
        return img

    def top_at(self, day) -> float:
        known = sorted((dd, v) for dd, v in self.days_top.items() if v is not None and dd <= day)
        return known[-1][1] if known else TOWER_BASE - self.h0

    def objective(self, d, ch: Chapter, t):
        """The objective panel at the bottom: slides in, stays for the reading time, slides out."""
        k, w, h = self.k, self.w, self.h
        a = ease(span(t, 0.25, 0.65)) * (1 - span(t, ch.dur - 0.45, ch.dur - 0.1))
        if a <= 0:
            return
        bw = round(1100 * k)
        lines = wrap(d, ch.detail, self.f_detail, bw - round(60 * k))[:2]
        bh = round(118 * k) + len(lines) * round(40 * k)
        x0 = (w - bw) / 2
        # high, in the sky between the corner panels and the tower's top: the yard and the tower stay clear
        y0 = round(205 * k) - round(50 * k) * (1 - ease(span(t, 0.25, 0.65)))
        self.panel(d, (x0, y0, x0 + bw, y0 + bh), a, GOLD if ch.pour else CYAN)
        d.text((x0 + round(30 * k), y0 + round(18 * k)), "OBJECTIVE · " + ch.tag, font=self.f_tag,
               fill=(*(GOLD if ch.pour else CYAN), int(255 * a)))
        d.text((x0 + round(30 * k), y0 + round(50 * k)), ch.title, font=self.f_title, fill=(*WHITE, int(255 * a)))
        for i, ln in enumerate(lines):
            d.text((x0 + round(30 * k), y0 + round(108 * k) + i * round(40 * k)), ln, font=self.f_detail,
                   fill=(*DIM, int(255 * a)))

    def activity_box(self, d, ch: Chapter, t):
        bb = ch.bbox
        if not bb or (bb[2] - bb[0]) * (bb[3] - bb[1]) > 0.3:
            return  # it's everywhere: a box round the whole picture says nothing
        k, w, h = self.k, self.w, self.h
        a = ease(span(t, 0.4, 0.9)) * (1 - span(t, ch.dur - 0.5, ch.dur - 0.1)) * 0.85
        if a <= 0:
            return
        x0, y0, x1, y1 = bb
        pad = 0.02
        x0, y0, x1, y1 = (max(0.01, x0 - pad) * w, max(0.2, y0 - pad) * h, min(0.99, x1 + pad) * w,
                          min(0.97, y1 + pad) * h)
        col = (*(GOLD if ch.pour else CYAN), int(255 * a))
        L = round(30 * k)
        for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            d.line([cx, cy, cx + sx * L, cy], fill=col, width=max(3, round(3 * k)))
            d.line([cx, cy, cx, cy + sy * L], fill=col, width=max(3, round(3 * k)))
        d.text((x0 + round(6 * k), y0 + round(6 * k)), "ACTIVITY", font=self.f_small, fill=col)

    def toast(self, d, text, t):
        k, w = self.k, self.w
        a = ease(span(t, 0.3, 0.6)) * (1 - span(t, 2.5, 3.0))
        if a <= 0:
            return
        x1 = w - round(24 * k)
        y = self.h - round(90 * k)   # bottom right: clear of the objective panel and the feed's corners
        tw = d.textlength(text, font=self.f_tag) + round(50 * k)
        self.panel(d, (x1 - tw, y, x1, y + round(54 * k)), a, GOLD)
        d.text((x1 - tw / 2, y + round(27 * k)), text, font=self.f_tag, fill=(*GOLD, int(255 * a)), anchor="mm")

    def skip_card(self, d, text, t):
        """Time skipped since the last stretch (idle hours, nights): centre, briefly."""
        k, w, h = self.k, self.w, self.h
        a = ease(span(t, 0.0, 0.15)) * (1 - span(t, 1.1, 1.5))
        if a <= 0:
            return
        d.text((w / 2, h * 0.42), text, font=self.f_big, fill=(*WHITE, int(255 * a)), anchor="mm",
               stroke_width=round(4 * k), stroke_fill=(*PANEL, int(255 * a)))

    def start_card(self, t) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        a = 1 - span(t, 1.6, 2.2)
        if a > 0:
            d.text((self.w / 2, self.h * 0.42), "LIVE SITE FEED", font=self.f_big, fill=(*GOLD, int(255 * a)),
                   anchor="mm", stroke_width=round(4 * self.k), stroke_fill=(*PANEL, int(255 * a)))
            d.text((self.w / 2, self.h * 0.42 + round(80 * self.k)), "ONLY THE MOMENTS SOMETHING HAPPENED",
                   font=self.f_tag, fill=(*WHITE, int(255 * a)), anchor="mm", stroke_width=round(3 * self.k),
                   stroke_fill=(*PANEL, int(255 * a)))
        return img


def skip_text(gap_s: float) -> str:
    h, m = int(gap_s // 3600), int(gap_s % 3600 // 60)
    if h >= 24:
        return f"+{h // 24} D {h % 24} H"
    return f"+{h} H {m:02d} M" if h else f"+{m} MIN"


# --- the ending ---------------------------------------------------------------------------------------------------

def ending(w, h, first: np.ndarray, last: np.ndarray, labels, growth, t: float) -> np.ndarray:
    k = h / 1080
    mid = sum(TOWER_COLS) / 2  # the wipe stops down the middle of the pedestal: before on its left, now on its right
    xw = int(w * (0.95 - (0.95 - mid) * ease(span(t, 0.4, 2.4))))
    out = last.copy()
    out[:, :xw] = first[:, :xw]
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle([xw - 2, 0, xw + 2, h], fill=(*GOLD, 255))
    f_tag = ImageFont.truetype(GAME, round(28 * k))
    f_big = ImageFont.truetype(GAME, round(60 * k))
    f_mid = ImageFont.truetype(GAME, round(34 * k))
    m = round(40 * k)
    for x, lbl, anchor in ((m, labels[0], "la"), (w - m, labels[1], "ra")):
        d.text((x, m), lbl, font=f_tag, fill=(*WHITE, 255), anchor=anchor, stroke_width=3, stroke_fill=(*PANEL, 255))
    a = ease(span(t, 2.6, 3.1))
    if a > 0:
        cx, cy = w * 0.5, h * 0.8
        bw, bh = round(900 * k), round(170 * k)
        intro.panel(d, (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2), a, k, GOLD)
        d.text((cx, cy - round(40 * k)), f"PROGRESS SO FAR · PEDESTAL HEIGHT {growth:+.0%}", font=f_mid,
               fill=(*WHITE, int(255 * a)), anchor="mm")
        nxt = intro.STAGES[[s[0] for s in intro.STAGES].index(intro.NOW_STAGE) + 1][1]
        d.text((cx, cy + round(25 * k)), "NEXT UP: " + nxt, font=f_mid, fill=(*GOLD, int(255 * a)), anchor="mm")
        d.text((cx, cy + round(62 * k)), "TO BE CONTINUED", font=ImageFont.truetype(GAME, round(22 * k)),
               fill=(*DIM, int(255 * a)), anchor="mm")
    out = blend(out, ov)
    if t > END_S - 0.7:
        out = (out * (1 - span(t, END_S - 0.7, END_S))).astype(np.uint8)
    return out


# --- the pass -------------------------------------------------------------------------------------------------------

NOON_SPLIT_H = 12.5           # a day's chapters: before and after this hour


def plan(cfg, box, start, end):
    """Days scored, and the work grouped into chapters: a day's active segments before and after midday."""
    days = ac.scan(cfg, box, start, end)
    days = [d for d in days if d.samples]
    if not days:
        raise ValueError("nothing registered in that range")
    segs = [s for s in ac.segments(days) if s.score >= MIN_SCORE]
    groups: dict = {}
    for g in segs:
        dt = datetime.fromtimestamp(g.start)
        groups.setdefault((g.day, dt.hour + dt.minute / 60 >= NOON_SPLIT_H), []).append(g)
    chapters = []
    for key in sorted(groups):
        gs = groups[key]
        pours = [g.start for g in gs if sum(x.sky >= ac.SKY_MIN for x in g.samples) >= POUR_SKY]
        chapters.append(Chapter(gs, pours))
        log.info("chapter %s %s: %d segments, %d pours, score %.0f", key[0], "pm" if key[1] else "am", len(gs),
                 len(pours), chapters[-1].score)
    return days, chapters


def heights(cfg, box, days: list[ac.Day], w, h, ai: bool, cache: Path) -> dict:
    """The pedestal's top each day (box-relative 0-1, never dropping): Gemini reads it off a ruler drawn on the
    day's midday view (focus.ruler_tops), snapped to the nearest strong edge; without AI, the skyline over it (which
    only works once it's above the trees)."""
    from .focus import ruler_tops, snap
    views = []
    for d in days:
        noon = datetime.combine(d.day, datetime.min.time()).timestamp() + 13 * 3600
        s = min(d.samples, key=lambda s: abs(s.ts - noon))
        views.append(view(cv2.imread(str(cfg.paths.frames / s.file), cv2.IMREAD_COLOR), np.array(s.m), box, w, h))
    sub = (TOWER_COLS[0], 0.0, TOWER_COLS[1], TOWER_BASE)
    if ai:
        reads = ruler_tops(cfg, "concrete water tower pedestal (the cylinder on the left)", views, cache)
        tops = [snap(v, r, sub) if r is not None else None for v, r in zip(views, reads)]
    else:
        ac.structure_top(days, TOWER_COLS)
        tops = [d.top for d in days]
    out, best = {}, None
    for d, t in zip(days, tops):
        if t is not None:
            best = t if best is None else min(best, t)
        out[d.day] = best
        log.info("  %s: pedestal top %s", d.day, None if best is None else round(best, 3))
    return out


def lifts(days_top: dict, box=BOX) -> dict:
    """The pedestal's concrete lifts each measured day. The measured top is the climbing form's, which stands a lift
    above the concrete (and covers the top two)."""
    per = LIFT_PX / (box[3] - box[1])
    now = datetime.now()
    # today's reading is from the latest frame until its midday one exists: skip it until the afternoon, so the
    # intro (cached by its schedule) doesn't flip between runs
    return {d: max(0, round((TOWER_BASE - top) / per - 1)) for d, top in days_top.items()
            if top is not None and (d < now.date() or now.hour >= 14)}


def intro_schedule(days: list[ac.Day], chapters: list[Chapter], days_top: dict, box=BOX, dlog=None) -> dict:
    """The intro's replay from every pour seen (not only the chapters that make the cut), the measured lifts and
    what Claude saw each day (site_ai.day_log)."""
    pours = sorted(p for c in chapters for p in c.pours)
    last = max([d.day for d in days], default=datetime.now().date())
    seen = [(d, e["kind"], e["caption"]) for d, v in sorted((dlog or {}).items()) for e in v["events"]]
    return intro.schedule(pours, lifts(days_top, box), last, trees_cleared(days), seen)


def day_log(cfg, days: list[ac.Day], chapters: list[Chapter], box, w, h, ai: bool) -> dict:
    """What Claude saw each day (cached; {} without AI or a key)."""
    if not ai:
        return {}
    return site_ai.day_log(cfg, days, box, w, h, sorted(p for c in chapters for p in c.pours), intro.facts_text(),
                           state_dir(cfg) / "site" / "day_log.json")


def intro_check(cfg, box=BOX, still: float | None = None, ai: bool = True) -> dict:
    """Quick looks at the intro: one still at `still` seconds with its HUD (work\\intro3d\\...\\still_*_hud.png), or
    without it the whole intro at draft quality with its HUD (work\\site\\intro_draft.mp4)."""
    w, h = int(cfg.output.width), int(cfg.output.height)
    start = datetime.fromisoformat(sorted(p.name for p in cfg.paths.frames.iterdir()
                                          if p.is_dir() and p.name[:2] == "20")[0]).timestamp()
    days, chapters = plan(cfg, box, start, time.time())
    days_top = heights(cfg, box, days, w, h, ai, state_dir(cfg) / "site" / "heights.json")
    sched = intro_schedule(days, chapters, days_top, box, day_log(cfg, days, chapters, box, w, h, ai))
    res = {"lifts": f"{sched['lifts0']} -> {sched['lifts_now']}", "pours": len(sched["pours"]),
           "days": len(sched["days"])}
    if still is not None:
        out = intro.render(cfg, sched, still=still)
        p = out / f"still_{still:05.1f}.png"
        img = cv2.resize(cv2.imread(str(p), cv2.IMREAD_COLOR), (w, h))
        hp = p.with_name(p.stem + "_hud.png")
        cv2.imwrite(str(hp), blend(img, intro.HUD(w, h, sched).frame(still)))
        return {**res, "still": str(hp)}
    dst = cfg.paths.work / "site" / "intro_draft.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen(ffmpeg_cmd(cfg, "nvenc" if nvenc_available(cfg) else "x264", w, h, FPS, dst,
                                      deflicker=False), stdin=subprocess.PIPE, creationflags=NO_WINDOW)
    try:
        for img in intro.frames(cfg, w, h, sched, draft=True):
            enc.stdin.write(img.tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
    return {**res, "draft": str(dst)}


def trees_cleared(days: list[ac.Day]):
    """The day the pedestal's top came up past the trees behind it: the skyline over it stays on the treetops
    until then, and starts climbing after (None if it hasn't yet)."""
    sk = [(d.day, float(np.median(d.skyline[int(len(d.skyline) * TOWER_COLS[0]):
                                             int(len(d.skyline) * TOWER_COLS[1])])))
          for d in days if d.skyline]
    if not sk:
        return None
    trees = sk[0][1]
    return next((day for day, y in sk if y < trees - 4), None)


def footage(ch: Chapter, rows) -> list:
    """The captured frames in the chapter's active stretches (a minute either side of an active check), each with
    the mapping of the nearest scored sample."""
    samples = ch.samples
    act = [s.ts for s in samples if s.active]
    sts = [s.ts for s in samples]
    out = []
    for f in rows:
        if f.ts < ch.start - 60 or f.ts > ch.end + 60:
            continue
        i = bisect.bisect_left(act, f.ts)
        near = min(abs(f.ts - act[j]) for j in (i - 1, i) if 0 <= j < len(act))
        if near > 60:
            continue
        j = bisect.bisect_left(sts, f.ts)
        j = min(range(max(0, j - 1), min(len(sts), j + 1)), key=lambda q: abs(sts[q] - f.ts))
        out.append((f, np.array(samples[j].m, np.float64)))
    return out


STEADY_MAX_PX = 24.0          # an alignment moving the view more than this is a mismatch, not registration error


class Steady:
    """A chapter's frames held still and upscaled. Scoring maps one frame per 10 s, and the frames between borrowed
    that mapping, so the camera's shake in the wind showed, and each 10 min SIFT keyframe can be off by up to ~10 px
    and 0.3 deg: each frame is aligned (ECC: rotation + shift at half size, then affine at full size, which also takes
    out the rolling shutter's shear on a windy day) to the chapter's median view on the ground below the skyline, cropped once at camera resolution, then upscaled (Real-ESRGAN x2, or a plain cubic resize and a little
    sharpening without `up`)."""

    def __init__(self, box, frames: list, skyline: list[int], w: int, h: int, up=None):
        self.box, self.frames, self.w, self.h, self.up = box, frames, w, h, up
        self.size = (box[2] - box[0], box[3] - box[1])
        self.half = (self.size[0] // 2, self.size[1] // 2)
        hw, hh = self.half
        self.mask = np.zeros((hh, hw), np.uint8)
        if len(skyline) == hw:  # the day's skyline is in half-size box rows already
            self.mask[np.arange(hh)[:, None] >= np.array(skyline)[None, :] + 4] = 255
        else:
            self.mask[hh // 2:] = 255
        self.mask_full = cv2.resize(self.mask, self.size, interpolation=cv2.INTER_NEAREST)
        self.anchor_full = None
        # the median of a few frames, then again with those aligned to it (sharper)
        idx = np.linspace(0, len(frames) - 1, min(9, len(frames))).astype(int)
        crops = [self.crop(*self.read(i)) for i in idx]
        self.anchor = np.median([self.grey(c) for c in crops], axis=0).astype(np.float32)
        for c in crops:
            W = self.align(c)
            if W is not None:
                c[:] = cv2.warpAffine(c, W[:2], self.size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                      borderMode=cv2.BORDER_REFLECT)
        self.anchor = np.median([self.grey(c) for c in crops], axis=0).astype(np.float32)
        self.anchor_full = np.median([self.grey_full(c) for c in crops], axis=0).astype(np.float32)
        self.missed = 0

    def read(self, i):
        f, m = self.frames[i]
        x0, y0 = self.box[:2]
        A = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64) @ np.vstack([m, [0, 0, 1]])
        return cv2.imread(str(f.path), cv2.IMREAD_COLOR), A

    def crop(self, img, A, flags=cv2.INTER_LINEAR):
        return cv2.warpAffine(img, A[:2], self.size, flags=flags, borderMode=cv2.BORDER_REFLECT)

    def grey(self, crop):
        g = cv2.cvtColor(cv2.resize(crop, self.half, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(g, (0, 0), 1.0).astype(np.float32)

    @staticmethod
    def grey_full(crop):
        return cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (0, 0), 0.8).astype(np.float32)

    def align(self, crop) -> np.ndarray | None:
        """3x3: where each anchor pixel is in this crop (full size), or None when ECC doesn't settle."""
        try:
            cc, W = cv2.findTransformECC(self.anchor, self.grey(crop), np.eye(2, 3, dtype=np.float32),
                                         cv2.MOTION_EUCLIDEAN,
                                         (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), self.mask, 3)
        except cv2.error:
            return None
        W = np.vstack([W.astype(np.float64), [0, 0, 1]])
        W[:2, 2] *= 2  # half size -> full (a rotation is the same at any size)
        if cc < 0.6 or math.hypot(W[0, 2], W[1, 2]) > STEADY_MAX_PX:
            return None
        if self.anchor_full is not None:
            try:
                _, F = cv2.findTransformECC(self.anchor_full, self.grey_full(crop), W[:2].astype(np.float32),
                                            cv2.MOTION_AFFINE,
                                            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-6),
                                            self.mask_full, 1)
                F = np.vstack([F.astype(np.float64), [0, 0, 1]])
                if math.hypot(*(F[:2, 2] - W[:2, 2])) < 2:   # a refinement, not a different answer
                    W = F
            except cv2.error:
                pass
        return W

    def __call__(self, i: int) -> np.ndarray:
        img, A = self.read(i)
        W = self.align(self.crop(img, A))
        if W is None:
            self.missed += 1
        else:
            A = np.linalg.inv(W) @ A
        c = self.crop(img, A, cv2.INTER_LANCZOS4)
        if self.up is None:
            out = cv2.resize(c, (self.w, self.h), interpolation=cv2.INTER_CUBIC)
            return cv2.addWeighted(out, 1.5, cv2.GaussianBlur(out, (0, 0), 1.6), -0.5, 0)
        out = self.up(c)
        if out.shape[:2] != (self.h, self.w):
            out = cv2.resize(out, (self.w, self.h), interpolation=cv2.INTER_AREA)
        return out


def run(cfg, start: float | None = None, end: float | None = None, box=BOX, max_s: float = MAX_FOOTAGE_S,
        ai: bool = True, preview: float | None = None, use_intro: bool = True, intro_only: bool = False,
        upscale: bool = True) -> dict:
    t_start = time.time()
    w, h = int(cfg.output.width), int(cfg.output.height)
    end = end or time.time()
    if start is None:
        dirs = sorted(p.name for p in cfg.paths.frames.iterdir() if p.is_dir() and p.name[:2] == "20")
        start = datetime.fromisoformat(dirs[0]).timestamp()
    sd = state_dir(cfg) / "site"
    sd.mkdir(parents=True, exist_ok=True)
    work = cfg.paths.work / "site"
    work.mkdir(parents=True, exist_ok=True)

    days, chapters = plan(cfg, box, start, end)
    days_top = heights(cfg, box, days, w, h, ai, sd / "heights.json")
    dlog = day_log(cfg, days, chapters, box, w, h, ai)
    sched = intro_schedule(days, chapters, days_top, box, dlog) if use_intro else None
    if intro_only:
        chapters = []
    else:
        rows = [f for f in daylight(captured(cfg, start, end)) if f.path.exists()]
        for ch in chapters:
            ch.frames = footage(ch, rows)
        chapters = [c for c in chapters if len(c.frames) >= 3]
        # three views of each for the caption: its start, its busiest moment, its end
        imgs = []
        for ch in chapters:
            pk = max(ch.samples, key=lambda s: s.score)
            picks = [ch.frames[0], min(ch.frames, key=lambda fm: abs(fm[0].ts - pk.ts)), ch.frames[-1]]
            imgs.append([view(cv2.imread(str(f.path), cv2.IMREAD_COLOR), m, box, w, h) for f, m in picks])
        # what the caption should know: which pour this is, how much taller the pedestal is than on day 1
        context, n_pours = [], 0
        base0 = next((v for v in days_top.values() if v is not None), None)
        cleared = trees_cleared(days)
        for ch in chapters:
            bits = []
            if cleared is not None:
                d_ = ch.segs[0].day
                bits.append("Its top is still below the trees behind it." if d_ < cleared else
                            "Today its top rises above the trees behind it." if d_ == cleared else
                            "It now stands above the tree line.")
            if ch.pours:
                a_ = n_pours + 1
                n_pours += len(ch.pours)
                bits.append(f"This is concrete pour {a_ if len(ch.pours) == 1 else f'{a_}-{n_pours}'} seen so far.")
            top = days_top.get(ch.segs[0].day)
            if base0 is not None and top is not None and base0 > top:
                bits.append(f"The pedestal is now {(TOWER_BASE - top) / (TOWER_BASE - base0):.1f}x as tall as on "
                            f"the first day.")
            seen = dlog.get(ch.segs[0].day)
            if seen and seen.get("summary"):
                bits.append(f"That day overall: {seen['summary']}")
            context.append(" ".join(bits))
        captions(cfg, chapters, imgs, ai, sd / "captions.json", context)
        # timing: every chapter its reading time (a pour or two: a little longer); drop the weakest until it fits
        for ch in chapters:
            ch.dur = read_s(ch.words) + CAPTION_GAP_S + 1.5 * len(ch.pours)
        keep = sorted(chapters, key=lambda c: (c.pour, c.score), reverse=True)
        while keep and sum(c.dur for c in keep) > max_s:
            keep.pop()
        chapters = sorted(keep, key=lambda c: c.start)
        log.info("%d chapters, %.0f s of footage (%d pours)", len(chapters), sum(c.dur for c in chapters),
                 sum(len(c.pours) for c in chapters))

    pours = sorted(p_ for c in chapters for p_ in c.pours)
    up = None
    if upscale and chapters:
        try:
            from .upscale import Upscaler
            up = Upscaler(cfg, 2)
            if up.dev != "cuda":
                log.warning("no CUDA: plain 2x instead of Real-ESRGAN")
                up = None
        except Exception as e:  # noqa: BLE001 - torch missing or the download failed: the plain resize still works
            log.warning("upscaler unavailable (%s): plain 2x", e)
    skylines = {d.day: d.skyline for d in days}
    pool = ThreadPoolExecutor(2)   # the next frames load, align and upscale while this one gets its HUD
    first_day = days[0].day if days else datetime.fromtimestamp(start).date()
    hud = FeedHUD(w, h, first_day, days_top, pours)

    if preview:
        dst = work / "site_preview.mp4"
        job = None
    else:
        job = {**cj.new_job(chapters[0].start if chapters else start, chapters[-1].end if chapters else end,
                            None, None), "subject": "water tower", "style": "site"}
        dst = cfg.paths.custom / cj.file_name(job)
    silent = work / f"{dst.stem}.video.mp4"
    wav = work / f"{dst.stem}.wav"
    encoder = "nvenc" if cfg.output.encoder in ("auto", "nvenc") and nvenc_available(cfg) else "x264"
    enc = subprocess.Popen(ffmpeg_cmd(cfg, encoder, w, h, FPS, silent, deflicker=False), stdin=subprocess.PIPE,
                           creationflags=NO_WINDOW)
    limit = round(preview * FPS) if preview else None
    out_ts: list[float] = []
    n = 0

    def put(img, ts):
        nonlocal n
        if limit is not None and n >= limit:
            return False
        enc.stdin.write(img.tobytes())
        out_ts.append(ts)
        n += 1
        return True

    try:
        if use_intro:
            for img in intro.frames(cfg, w, h, sched):
                if not put(img, start):
                    break
        lumas: list[float] = []
        prev_end = None
        t_feed = 0.0
        for ci, ch in enumerate(chapters):
            if limit is not None and n >= limit:
                break
            n_out = round(ch.dur * FPS)
            fr_ = ch.frames
            # spread the chapter's frames over its time: with more frames than slots each slot shows its nearest one,
            # with fewer they cross-fade
            pos = np.linspace(0, len(fr_) - 1, n_out)
            if len(fr_) > n_out:
                pos = np.round(pos)
            used = sorted({int(math.floor(p)) for p in pos} |
                          {min(len(fr_) - 1, int(math.floor(p)) + 1) for p in pos if p % 1 >= 0.02})
            steady = Steady(box, fr_, skylines.get(ch.segs[0].day, []), w, h, up)
            futs: dict = {}

            def get(i):
                k = used.index(i)
                for j in used[k:k + 8]:
                    if j not in futs:
                        futs[j] = pool.submit(steady, j)
                for j in [j for j in futs if j < i - 1]:
                    del futs[j]
                return futs[i].result()

            skip = skip_text(ch.start - prev_end) if prev_end is not None and ch.start - prev_end > 600 else None
            pour_at = list(ch.pours)
            t_pour = None
            for j in range(n_out):
                p = pos[j]
                i0 = int(math.floor(p))
                i1 = min(len(fr_) - 1, i0 + 1)
                fa = p - i0
                v = get(i0) if fa < 0.02 or i1 == i0 else cv2.addWeighted(get(i0), 1 - fa, get(i1), fa, 0)
                # auto exposure flicker: scale toward the running average of the last frames
                lumas.append(float(v[::8, ::8].mean()))
                target = float(np.mean(lumas[-12:]))
                if lumas[-1] > 1:
                    v = np.clip(v.astype(np.float32) * (0.5 + 0.5 * target / lumas[-1]), 0, 255).astype(np.uint8)
                ts = fr_[i0][0].ts + fa * (fr_[i1][0].ts - fr_[i0][0].ts)
                t_ch = j / FPS
                if pour_at and ts >= pour_at[0]:
                    pour_at.pop(0)
                    t_pour = 0.0
                frame = blend(v, hud.frame(ts, ch, t_ch, t_feed, skip, t_pour))
                if t_pour is not None:
                    t_pour += 1 / FPS
                if ci == 0 and use_intro and t_ch < 2.2:
                    frame = blend(frame, hud.start_card(t_ch))
                    if t_ch < 0.25:  # out of the intro: a flash
                        frame = cv2.addWeighted(frame, t_ch / 0.25, np.full_like(frame, 255), 1 - t_ch / 0.25, 0)
                if not put(frame, ts):
                    break
                t_feed += 1 / FPS
            prev_end = ch.end
            log.info("chapter %d/%d: %s (%.1f s, %d frames, %d not aligned)", ci + 1, len(chapters), ch.title,
                     ch.dur, len(used), steady.missed)
        if not preview and chapters and len(days) >= 2:
            # the first registered day at midday against the latest
            d0, d1 = days[0], days[-1]
            two = []
            for dd in (d0, d1):
                noon = datetime.combine(dd.day, datetime.min.time()).timestamp() + 13 * 3600
                s = min(dd.samples, key=lambda s: abs(s.ts - noon))
                two.append((SimpleNamespace(path=cfg.paths.frames / s.file), np.array(s.m, np.float64)))
            st = Steady(box, two, d1.skyline, w, h, up)   # aligned to each other, so the wipe doesn't jump
            imgs = [st(0), st(1)]
            t0, t1 = days_top.get(d0.day), days_top.get(d1.day)
            growth = (TOWER_BASE - t1) / (TOWER_BASE - t0) - 1 if t0 is not None and t1 is not None else 0.0
            labels = (f"{d0.day:%a %b %d}".upper(), f"{d1.day:%a %b %d}".upper())
            for j in range(round(END_S * FPS)):
                put(ending(w, h, imgs[0], imgs[1], labels, growth, j / FPS), chapters[-1].end)
    finally:
        pool.shutdown(cancel_futures=True)
        enc.stdin.close()
        rc = enc.wait()
    if rc != 0:
        raise RuntimeError(f"encoder exited {rc}")
    total_s = n / FPS
    synth(total_s, [], [], wav)
    tmp = dst.with_name(dst.stem + ".part.mp4")
    r = subprocess.run([cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent), "-i",
                        str(wav), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags",
                        "+faststart", str(tmp)], creationflags=NO_WINDOW)
    silent.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("muxing the sound failed")
    tmp.replace(dst)
    res = {"output": str(dst), "duration_s": round(total_s, 1), "frames": n, "chapters": len(chapters),
           "pours": len(pours), "elapsed_s": round(time.time() - t_start, 1)}
    if job:
        render_index.index_dir(cfg).mkdir(exist_ok=True)
        write_json(render_index.index_path(cfg, dst.name), {
            "output": dst.name, "window_start_ts": job["start_ts"], "window_end_ts": job["end_ts"], "base_fps": FPS,
            "interp_factor": 1, "frame_ts": [round(t, 3) for t in out_ts]})
        cj.write_job(cfg, {**job, "status": "ok", "progress": 1.0, "output": dst.name, "duration_s": total_s,
                           "seconds": round(total_s, 1), "frames_used": n, "finished": time.time()})
        res["job"] = job["id"]
    log.info("site -> %s (%.0f s, %d chapters, %.0f s)", dst.name, total_s, len(chapters), res["elapsed_s"])
    return res
