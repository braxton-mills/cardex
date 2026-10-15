"""Annotated timelapses: `campi annotate <video>` writes a copy of a rendered video with an overlay that says what's
happening (venv-sightings: numpy, PIL, google-genai).

The render index (render_index.py) gives the capture time of every encoded frame, so every overlay element follows
"the capture time on screen now":
- a header (weekday, date, clock) and a running count of the cars seen so far (sightings);
- a timeline bar across the whole range: night shading, cars per hour, day ticks and a playhead;
- captions that fade in at the moment something happened: sunrise / sunset (exposure, else luma), the busiest hour
  and a wrap-up of each day, rare finds (labels seen exactly once), the user's car ([annotate] my_car), the camera
  going offline, and Gemini's notes on a frame every ~2.5 h (weather, light, anything unusual; --no-ai skips them).
Captions are placed by priority in two lanes; ones that can't be shown near their moment are dropped. The schedule
and Gemini's answers are kept in state\\annotate\\ (the answers are reused, so a re-render costs nothing).

A custom timelapse's annotated copy becomes its own custom job (so the app lists and seeks it); other videos get
<stem>_annotated.mp4 next to them.
"""
from __future__ import annotations

import bisect
import logging
import math
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import custom_jobs as cj
from . import frames as fr
from . import render_index
from .config import NO_WINDOW, read_json, write_json
from .render import ffmpeg_cmd, ffprobe, nvenc_available

log = logging.getLogger("annotate")

MY_CAR = "alfa romeo giulia"   # label of the user's own car (captions say "Your Giulia")
NIGHT_EXPOSURE = 0.05          # s x gain above this = night (rows from before the exposure column use luma)
NIGHT_LUMA = 40.0
OFFLINE_S = 30 * 60            # a gap between captured frames longer than this is "camera offline"
AI_EVERY_S = 2.5 * 3600
CAPTION_S, FADE_S, SLACK_S, LANES = 2.2, 0.3, 1.0, 2
# priority (lower first) and the small label above each caption kind
KINDS = {"offline": (0, "CAMERA OFFLINE"), "mycar": (1, "YOUR CAR"), "busy": (2, "RUSH HOUR"),
         "ai_notable": (3, "ON CAMERA"), "rare": (4, "RARE FIND"), "sun": (5, ""), "wrap": (6, "DAY WRAP"),
         "ai": (7, "ON CAMERA")}
ACCENT = {"offline": (255, 93, 108), "mycar": (52, 211, 153), "busy": (255, 122, 89), "ai_notable": (34, 211, 238),
          "rare": (255, 207, 90), "sun": (255, 180, 90), "wrap": (139, 92, 246), "ai": (34, 211, 238)}
TIMED = {"offline", "rare"}  # kinds whose label carries the time (the others say it in the text)
FONT = r"C:\Windows\Fonts\SegUIVar.ttf"
BAR_RGB = (8, 9, 18)


def state_dir(cfg) -> Path:
    d = cfg.paths.state / "annotate"
    d.mkdir(parents=True, exist_ok=True)
    return d


def hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def hour_name(h: int) -> str:
    return f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"


# --- finding the video ----------------------------------------------------------------------------------------------

def resolve(cfg, video: str) -> tuple[Path, dict | None]:
    """A custom job id, a file name in the output folders, or a path -> (video path, custom job or None)."""
    job = cj.read_job(cfg, video) if cj.ID_RE.match(video) else None
    if job:
        return cfg.paths.custom / job["output"], job
    p = Path(video)
    if not p.is_file():
        p = next((d / video for d in (cfg.paths.custom, cfg.paths.daily, cfg.paths.out) if (d / video).is_file()), p)
    if not p.is_file():
        raise FileNotFoundError(f"no video {video}")
    m = cj.CUSTOM_RE.match(p.name)
    return p, cj.read_job(cfg, m.group(1)) if m else None


# --- what happened --------------------------------------------------------------------------------------------------

def captured(cfg, start: float, end: float) -> list[fr.Frame]:
    """Every frame captured in the range (index rows; the video only shows a sample of them)."""
    d = datetime.fromtimestamp(start).date()
    rows = []
    while d <= datetime.fromtimestamp(end).date():
        rows += [f for f in fr.load_day(cfg, d) if start <= f.ts < end]
        d += timedelta(days=1)
    rows.sort(key=lambda f: f.ts)
    return rows


def night_spans(rows: list[fr.Frame]) -> list[tuple[float, float]]:
    """Night as (start, end) spans from every captured frame's exposure (or luma), smoothed over ~20 min."""
    if not rows:
        return []
    ts = np.array([f.ts for f in rows])
    dark = np.array([(f.exposure > NIGHT_EXPOSURE) if f.exposure is not None else (f.luma < NIGHT_LUMA)
                     for f in rows], float)
    # majority over a 20-minute window (frame rates differ by day: thinned days have 1 a minute)
    lo = np.searchsorted(ts, ts - 600)
    hi = np.searchsorted(ts, ts + 600)
    c = np.concatenate([[0], np.cumsum(dark)])
    night = (c[hi] - c[lo]) / np.maximum(hi - lo, 1) > 0.5
    spans, cur = [], None
    for t, n in zip(ts, night):
        if n and cur is None:
            cur = t
        elif not n and cur is not None:
            spans.append((cur, t))
            cur = None
    if cur is not None:
        spans.append((cur, ts[-1]))
    return [(a, b) for a, b in spans if b - a > 1800]


def traffic(cfg, start: float, end: float):
    """(counted sighting times in range, per-label sightings {label lower: [ts]}, known labels)."""
    from .card_meshes import _ReadOnlyStore
    from .ui.sightings_data import Sightings
    cat = Sightings(cfg, _ReadOnlyStore(cfg)).catalog()
    starts = [t for t in cat["starts"] if start <= t < end]
    by = {k: sorted(x["ts"] for x in v) for k, v in cat["by"].items()}
    return starts, by, cat["known"]


def build_events(frame_ts: list[float], rows: list[fr.Frame], starts: list[float], by: dict, known: dict,
                 nights: list[tuple[float, float]]) -> list[dict]:
    start, end = frame_ts[0], frame_ts[-1]
    ev = []

    def add(ts, kind, text, label=None):
        if start <= ts <= end:
            label = KINDS[kind][1] if label is None else label
            ev.append({"ts": ts, "kind": kind, "label": f"{label} · {hm(ts)}" if kind in TIMED else label,
                       "text": text})

    for a, b in nights:  # a night that runs into the range start isn't a sunset
        if a > start + 600:
            add(a, "sun", f"Night falls · {hm(a)}", "SUNSET")
        if b < end - 600:
            add(b, "sun", f"Daylight · {hm(b)}", "SUNRISE")

    # per local day: busiest hour, and a wrap-up at midnight
    days: dict = {}
    for t in starts:
        dt = datetime.fromtimestamp(t)
        days.setdefault(dt.date(), Counter())[dt.hour] += 1
    best_day = max(days, key=lambda d: sum(days[d].values()), default=None)
    for d, hours in days.items():
        total = sum(hours.values())
        h, n = hours.most_common(1)[0]
        name = d.strftime("%A")
        if n >= 20:
            add(datetime(d.year, d.month, d.day, h, 30).timestamp(), "busy",
                f"{hour_name(h)}–{hour_name(h + 1)} · {n} cars, the busiest hour of {name}")
        midnight = datetime(d.year, d.month, d.day) + timedelta(days=1)
        if total >= 50:
            add(midnight.timestamp() - 60, "wrap",
                f"{name}: {total:,} cars" + (" · the busiest day so far" if d == best_day else ""))

    # rare finds: labels seen exactly once ever (make/model ones), and the user's car once a day
    for key, ts in by.items():
        k = known.get(key) or {}
        vague = {"", "n/a", "unknown"}
        if len(ts) == 1 and not k.get("generic") and (k.get("make") or "").lower() not in vague \
                and (k.get("model") or "").lower() not in vague:
            add(ts[0], "rare", k.get("label") or key)
    seen_days = set()
    for t in by.get(MY_CAR, []):
        d = datetime.fromtimestamp(t).date()
        if d not in seen_days and start <= t <= end:
            seen_days.add(d)
            add(t, "mycar", f"Your Giulia goes by · {hm(t)}")

    for a, b in zip(rows, rows[1:]):
        if b.ts - a.ts > OFFLINE_S:
            gap, a = b.ts - a.ts, a.ts
            add(a, "offline", f"No frames for {int(gap // 3600)} h {int(gap % 3600 // 60)} m"
                if gap >= 3600 else f"No frames for {int(gap // 60)} min")
    return ev


# --- Gemini ---------------------------------------------------------------------------------------------------------

AI_PROMPT = """These are frames from a fixed street camera, oldest first, taken at the local times listed. For each
frame write one note of at most 8 words about what a viewer would find interesting: weather (rain, wet road, fog,
snow, sun glare, the kind of sky), how busy the road is, or anything unusual (parked or working vehicles, people,
roadwork, emergency vehicles, decorations). Sunrise, sunset, day and night are shown separately, so never note them
alone; at night describe only weather or something unusual, else write "-". Mark notable=true only when something is
unusual or has clearly changed since the previous frame. Don't mention the camera, the timestamp or image quality.

Frames: {times}"""


def ai_notes(cfg, frame_ts: list[float], cache: Path) -> list[dict]:
    """Gemini's notes on one source frame every AI_EVERY_S ([{ts, note, notable}]), cached per video."""
    have = read_json(cache) if cache.is_file() else None
    if isinstance(have, list):
        return worth_showing(have)
    import cv2
    from google import genai
    from google.genai import errors, types
    from pydantic import BaseModel

    from .sightings_cloud import THINKING

    class Note(BaseModel):
        index: int
        note: str
        notable: bool

    start, end = frame_ts[0], frame_ts[-1]
    picks, t = [], start + AI_EVERY_S / 2
    frames = fr.frames_between(cfg, start, end)
    fts = [f.ts for f in frames]
    while t < end and frames:
        f = frames[min(bisect.bisect_left(fts, t), len(frames) - 1)]
        if not picks or f is not picks[-1]:  # an offline stretch maps several times to the same frame
            picks.append(f)
        t += AI_EVERY_S
    by_day: dict = {}
    for f in picks:
        by_day.setdefault(datetime.fromtimestamp(f.ts).date(), []).append(f)
    key = Path(cfg.sightings.cloud.api_key_file).read_text(encoding="utf-8").strip()
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=60_000))
    out, think_i = [], 0
    for d, day in by_day.items():
        parts = []
        for f in day:
            img = cv2.imread(str(f.path), cv2.IMREAD_COLOR)
            h, w = img.shape[:2]
            s = 512 / max(h, w)
            img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
            parts.append(types.Part.from_bytes(data=cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1]
                                               .tobytes(), mime_type="image/jpeg"))
        times = "; ".join(f"{i}: {datetime.fromtimestamp(f.ts):%A %I:%M %p}" for i, f in enumerate(day))
        while True:
            think = THINKING[think_i]
            config = types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=list[Note], temperature=0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=types.ThinkingConfig(**think) if think else None)
            try:
                resp = client.models.generate_content(model=cfg.sightings.cloud.model, config=config,
                                                      contents=[*parts, AI_PROMPT.format(times=times)])
                break
            except errors.APIError as e:
                if e.code == 400 and "think" in str(e).lower() and think_i < len(THINKING) - 1:
                    think_i += 1
                    continue
                raise
        for n in resp.parsed or []:
            if 0 <= n.index < len(day) and n.note.strip():
                out.append({"ts": day[n.index].ts, "note": n.note.strip().rstrip("."), "notable": bool(n.notable)})
        log.info("gemini: %s, %d frames, %d notes", d, len(day), len(resp.parsed or []))
    out.sort(key=lambda n: n["ts"])
    write_json(cache, out)
    return worth_showing(out)


def worth_showing(notes: list[dict]) -> list[dict]:
    """Drop notes with nothing to say, and repeats of the previous one."""
    return [n for i, n in enumerate(notes) if n["note"] not in ("-", "")
            and not re.search(r"no (significant |visible )?(change|activity)", n["note"], re.I)
            and (i == 0 or n["note"].lower() != notes[i - 1]["note"].lower())]


# --- caption schedule -----------------------------------------------------------------------------------------------

def schedule(events: list[dict], frame_ts: list[float], fps: float, interp: int, duration: float) -> list[dict]:
    """Place captions by priority in LANES lanes, each within SLACK_S after its moment; the rest are dropped."""
    def vt(ts):  # video time at which a capture time is on screen
        return bisect.bisect_left(frame_ts, ts) * interp / fps

    lanes: list[list[tuple[float, float]]] = [[] for _ in range(LANES)]
    placed = []
    for e in sorted(events, key=lambda e: (KINDS[e["kind"]][0], e["ts"])):
        t0 = vt(e["ts"])
        done = False
        for step in range(int(SLACK_S / 0.1) + 1):
            s = t0 + step * 0.1
            if s + CAPTION_S > duration:
                break
            for li, lane in enumerate(lanes):
                if all(s + CAPTION_S + 0.15 <= a or s >= b + 0.15 for a, b in lane):
                    lane.append((s, s + CAPTION_S))
                    placed.append({**e, "t": round(s, 2), "lane": li})
                    done = True
                    break
            if done:
                break
    placed.sort(key=lambda c: c["t"])
    return placed


# --- drawing --------------------------------------------------------------------------------------------------------

class Fonts:
    def __init__(self, h: int):
        k = h / 1080

        def f(size, style):
            font = ImageFont.truetype(FONT, round(size * k))
            try:
                font.set_variation_by_name(style)
            except (OSError, ValueError):
                pass
            return font
        self.clock = f(86, "Bold Display")
        self.date = f(30, "Semibold Text")
        self.cap = f(19, "Bold Small")
        self.count = f(58, "Bold Display")
        self.caption = f(33, "Semibold Display")
        self.tick = f(20, "Semibold Text")


class Overlay:
    """Static layers drawn once; per frame only the moving parts are drawn into the dirty regions."""

    def __init__(self, w: int, h: int, start: float, end: float, starts: list[float], nights, events_all):
        self.w, self.h, self.start, self.end = w, h, start, end
        self.k = h / 1080
        self.f = Fonts(h)
        self.starts = starts
        k = self.k
        self.bar_h = round(108 * k)
        self.bar_y = h - self.bar_h
        self.px0, self.px1 = round(40 * k), w - round(40 * k)
        self.plot_y0, self.plot_y1 = self.bar_y + round(16 * k), self.bar_y + round(70 * k)
        self.static = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(self.static)  # opaque things only: drawing on RGBA replaces pixels, it doesn't blend

        def layer(draw):  # translucent things go on their own layer, composited over what's there
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw(ImageDraw.Draw(img))
            self.static.alpha_composite(img)
        # header and counter scrims (soft vertical gradients), bar background
        grad = np.linspace(0.62, 0, round(230 * k))[:, None]
        scrim = np.zeros((len(grad), w, 4), np.uint8)
        scrim[..., 3] = (grad * 255).astype(np.uint8)
        self.static.alpha_composite(Image.fromarray(scrim))
        d.rectangle([0, self.bar_y, w, h], fill=(*BAR_RGB, 255))
        d.line([0, self.bar_y, w, self.bar_y], fill=(40, 42, 54, 255), width=1)

        def shade_nights(ld):
            for a, b in nights:
                ld.rectangle([self.x(a), self.plot_y0 - round(6 * k), self.x(b), self.plot_y1], fill=(52, 64, 150, 120))
        layer(shade_nights)
        # cars per hour, as a filled area
        bins = max(1, math.ceil((end - start) / 3600))
        counts = np.bincount([min(bins - 1, int((t - start) // 3600)) for t in starts], minlength=bins)
        top = max(1, int(np.percentile(counts, 99)) if counts.any() else 1)
        pts = [(self.px0, self.plot_y1)]
        for i, c in enumerate(counts):
            y = self.plot_y1 - min(1.0, c / top) * (self.plot_y1 - self.plot_y0)
            pts += [(self.x(start + i * 3600), y), (self.x(start + (i + 1) * 3600), y)]
        pts.append((self.px1, self.plot_y1))
        layer(lambda ld: ld.polygon(pts, fill=(255, 122, 89, 150)))

        def marks(ld):  # a dot per caption shown, and day ticks with names
            for e in events_all:
                x = self.x(e["ts"])
                ld.ellipse([x - 3 * k, self.plot_y0 - 10 * k, x + 3 * k, self.plot_y0 - 4 * k],
                           fill=(*ACCENT[e["kind"]], 230))
            day = datetime.fromtimestamp(start).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            while day.timestamp() < end:
                x = self.x(day.timestamp())
                ld.line([x, self.plot_y0 - round(6 * k), x, self.plot_y1 + round(6 * k)], fill=(255, 255, 255, 90))
                noon = (day + timedelta(hours=12)).timestamp()
                if noon < end:
                    ld.text((self.x(noon), self.plot_y1 + round(19 * k)), f"{day:%a} {day.day}", font=self.f.tick,
                            fill=(200, 202, 220, 230), anchor="mm")
                day += timedelta(days=1)
        layer(marks)
        self.static_np = np.asarray(self.static).copy()
        # a dimmed copy of the plot: the part after the playhead is drawn dim
        # (faded toward the bar colour; the bar itself stays opaque over the video's own timestamp)
        dim = self.static_np.copy()
        y0, y1 = self.plot_y0 - round(12 * k), self.plot_y1 + round(8 * k)
        dim[y0:y1, :, :3] = (dim[y0:y1, :, :3] * 0.4 + np.array(BAR_RGB) * 0.6).astype(np.uint8)
        self.dim_np = dim
        self.regions = [(0, round(230 * k)), (self.bar_y - round(260 * k), h)]  # rows that can change

    def x(self, ts: float) -> float:
        return self.px0 + (ts - self.start) / max(1.0, self.end - self.start) * (self.px1 - self.px0)

    def frame(self, ts: float, captions: list[tuple[dict, float]]) -> np.ndarray:
        """RGBA overlay for the capture time ts with (caption, alpha 0-1) pairs."""
        k = self.k
        xp = int(round(self.x(ts)))
        ov = self.static_np.copy()
        ov[:, xp:] = self.dim_np[:, xp:]
        img = Image.fromarray(ov)
        d = ImageDraw.Draw(img)
        dt = datetime.fromtimestamp(ts)
        m = round(44 * k)
        d.text((m, round(30 * k)), f"{dt:%A} · {dt:%B} {dt.day}".upper(), font=self.f.date, fill=(235, 236, 248, 255))
        d.text((m - round(3 * k), round(64 * k)), hm(ts), font=self.f.clock, fill=(255, 255, 255, 255))
        n = bisect.bisect_right(self.starts, ts)
        d.text((self.w - m, round(34 * k)), "CARS SO FAR", font=self.f.cap, fill=(200, 202, 220, 255), anchor="ra")
        d.text((self.w - m, round(60 * k)), f"{n:,}", font=self.f.count, fill=(255, 255, 255, 255), anchor="ra")
        # playhead
        d.line([xp, self.plot_y0 - round(12 * k), xp, self.plot_y1 + round(4 * k)], fill=(255, 255, 255, 255),
               width=max(2, round(2 * k)))
        r = round(5 * k)
        d.ellipse([xp - r, self.plot_y0 - round(12 * k) - r, xp + r, self.plot_y0 - round(12 * k) + r],
                  fill=(255, 255, 255, 255))
        # captions: lane 0 is the lower one
        for c, a in captions:
            self.caption(img, c, a)
        return np.asarray(img)

    def caption(self, img: Image.Image, c: dict, alpha: float) -> None:
        k = self.k
        d = ImageDraw.Draw(img)
        pad, gap = round(18 * k), round(10 * k)
        tw = d.textlength(c["text"], font=self.f.caption)
        lw = d.textlength(c["label"], font=self.f.cap) if c["label"] else 0
        bw = round(max(tw, lw) + 2 * pad + 8 * k)
        bh = round((86 if c["label"] else 62) * k)
        x = round(40 * k) - round((1 - alpha) * 30 * k)  # slides in from the left as it fades in
        y = self.bar_y - round(22 * k) - bh - c["lane"] * (round(96 * k) + gap)
        a = int(255 * alpha)
        box = Image.new("RGBA", (bw + 1, bh + 1), (0, 0, 0, 0))  # drawn on its own, then composited in place
        bd = ImageDraw.Draw(box)
        bd.rounded_rectangle([0, 0, bw, bh], radius=round(14 * k), fill=(10, 11, 22, int(205 * alpha)))
        bd.rounded_rectangle([0, 0, round(6 * k), bh], radius=round(3 * k), fill=(*ACCENT[c["kind"]], a))
        tx, ty = pad + round(6 * k), round(12 * k)
        if c["label"]:
            bd.text((tx, ty), c["label"], font=self.f.cap, fill=(*ACCENT[c["kind"]], a))
            ty += round(26 * k)
        bd.text((tx, ty), c["text"], font=self.f.caption, fill=(255, 255, 255, a))
        img.alpha_composite(box, dest=(x, y))

    def blend(self, frame: np.ndarray, ov: np.ndarray) -> None:
        """Composite the RGBA overlay onto a BGR frame in place, only in the rows that can change."""
        for y0, y1 in self.regions:
            o = ov[y0:y1]
            a = o[..., 3:4].astype(np.uint16)
            if not a.any():
                continue
            rgb = o[..., 2::-1].astype(np.uint16)  # RGBA -> BGR
            f = frame[y0:y1].astype(np.uint16)
            frame[y0:y1] = ((f * (255 - a) + rgb * a + 127) // 255).astype(np.uint8)


# --- the pass -------------------------------------------------------------------------------------------------------

def run(cfg, video: str, ai: bool = True, preview: float | None = None, out: str | None = None,
        style: str = "clean") -> dict:
    t_start = time.time()
    src, job = resolve(cfg, video)
    idx = render_index.read(cfg, src.name)
    if not idx or len(idx.get("frame_ts") or []) < 2:
        raise ValueError(f"{src.name} has no render index (only rendered clips, daily and custom videos do)")
    if style == "forensic":
        from .forensic import run as run_forensic
        return run_forensic(cfg, src, job, ai, preview, out)
    frame_ts, interp = idx["frame_ts"], int(idx.get("interp_factor") or 1)
    probe = ffprobe(cfg, src)
    vs = next(s for s in probe["streams"] if s.get("codec_type") == "video")
    w, h = int(vs["width"]), int(vs["height"])
    num, den = (int(x) for x in vs["avg_frame_rate"].split("/"))
    fps = num / den
    duration = float(probe["format"]["duration"])
    start, end = frame_ts[0], frame_ts[-1]

    starts, by, known = traffic(cfg, start, end)
    rows = captured(cfg, start, end)
    nights = night_spans(rows)
    events = build_events(frame_ts, rows, starts, by, known, nights)
    sd = state_dir(cfg)
    if ai:
        for n in ai_notes(cfg, frame_ts, sd / f"{src.stem}.ai.json"):
            events.append({"ts": n["ts"], "kind": "ai_notable" if n["notable"] else "ai",
                           "label": f"{KINDS['ai'][1]} · {hm(n['ts'])}", "text": n["note"][:60]})
    caps = schedule(events, frame_ts, fps, interp, duration)
    write_json(sd / f"{src.stem}.captions.json", caps)
    log.info("%s: %d cars, %d nights, %d events, %d captions shown", src.name, len(starts), len(nights), len(events),
             len(caps))
    for c in caps:
        log.info("  %5.1f s  %-10s %s", c["t"], c["kind"], c["text"])

    if out:
        dst = Path(out)
    elif preview:
        dst = cfg.paths.work / f"{src.stem}_annotated_preview.mp4"
    elif job:
        new = {k: job[k] for k in ("start_ts", "end_ts", "speed", "seconds")}
        new = {**cj.new_job(new["start_ts"], new["end_ts"], new["speed"], new["seconds"]), "annotated_of": job["id"]}
        dst = cfg.paths.custom / cj.file_name(new)
    else:
        dst = src.with_name(f"{src.stem}_annotated.mp4")
    tmp = dst.with_name(dst.stem + ".part.mp4")

    ov = Overlay(w, h, start, end, starts, nights, caps)
    n_frames = round(min(duration, preview or duration) * fps)
    dec = subprocess.Popen([cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src),
                            "-frames:v", str(n_frames), "-vf", "scale=in_color_matrix=bt709:in_range=tv,format=bgr24",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE,
                           creationflags=NO_WINDOW)
    encoder = "nvenc" if cfg.output.encoder in ("auto", "nvenc") and nvenc_available(cfg) else "x264"
    enc = subprocess.Popen(ffmpeg_cmd(cfg, encoder, w, h, round(fps), tmp, deflicker=False), stdin=subprocess.PIPE,
                           creationflags=NO_WINDOW)
    size = w * h * 3
    k = 0
    try:
        while k < n_frames:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy()
            t = k / fps
            ts = frame_ts[min(len(frame_ts) - 1, k // interp)]
            on = []
            for c in caps:
                if c["t"] - 0.01 <= t <= c["t"] + CAPTION_S:
                    a = min(1.0, (t - c["t"]) / FADE_S, (c["t"] + CAPTION_S - t) / FADE_S)
                    on.append((c, max(0.0, a) ** 0.8))
            ov.blend(frame, ov.frame(ts, on))
            enc.stdin.write(frame.tobytes())
            k += 1
            if k % 300 == 0:
                log.info("annotate %s: %d/%d frames", dst.name, k, n_frames)
    finally:
        enc.stdin.close()
        rc = enc.wait()
        dec.kill()
    if rc != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"encoder exited {rc}")
    tmp.replace(dst)
    res = {"output": str(dst), "frames": k, "captions": len(caps), "events": len(events),
           "elapsed_s": round(time.time() - t_start, 1)}
    if job and not preview and not out:
        render_index.write(cfg, dst, start, end, frame_ts)
        cj.write_job(cfg, {**new, "status": "ok", "progress": 1.0, "output": dst.name, "duration_s": k / fps,
                           "frames_used": job.get("frames_used"), "frames_available": job.get("frames_available"),
                           "actual_speed": job.get("actual_speed"), "finished": time.time()})
        res["job"] = new["id"]
    log.info("annotated %s -> %s (%d frames, %.0f s)", src.name, dst.name, k, res["elapsed_s"])
    return res
