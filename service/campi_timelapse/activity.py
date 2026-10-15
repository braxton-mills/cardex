"""Construction activity in one part of the scene (venv-sightings: numpy, cv2): which daylight frames show movement
or progress there, grouped into work segments, for `campi site`.

- Every daylight frame (raw days: one per SCORE_STEP_S) is mapped onto a reference frame: SIFT (focus.Register) on a
  keyframe every KEY_S, phase correlation on the background in between. Scoring works on the box at half size.
- The day's skyline (the median frame's first strong edge from the top, per column) splits the box: clouds move, so
  above it only thin dark lines count (a pump boom or a crane jib against the sky: a small black-hat), below it a
  fine-detail difference from the previous scored frame (people, vehicles, forms), minus pixels that change all day
  (trees in the wind).
- Active samples merge into segments (gaps under MERGE_S), and segments too short to be work are dropped.
- The skyline over the structure also measures its height each day (the median frame, so nothing moving counts).

Results are cached per day in state\\annotate\\ (today's is redone while it is still collecting frames).
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from . import frames as fr
from .annotate import state_dir
from .config import read_json, write_json
from .focus import MIN_INLIERS, Register, daylight

log = logging.getLogger("activity")

SCORE_STEP_S = 10.0            # raw days: score one frame this often
KEY_S = 600.0                  # SIFT keyframe spacing; phase correlation in between
GROUND_T = 14.0                # fine-detail change (grey levels) that counts below the skyline
GROUND_MIN = 60                # active pixels (half size) for a sample to be work below the skyline
SKY_MIN = 150                  # thin dark pixels above it, in long parts (a boom; cloud flecks are short)
CHRONIC = 0.08                 # pixels changing in more than this share of a day's samples are ignored (trees)
MERGE_S = 20 * 60              # active samples closer than this are one work session
MIN_SEG_S = 90                 # shorter sessions are a car passing through, not work
MIN_SEG_N = 3
VERSION = 1                    # bump to redo the cached days


@dataclass
class Sample:
    ts: float
    file: str                   # frames-relative path
    m: list                     # 2x3 camera -> reference pixels (full size)
    ground: int = 0
    sky: int = 0
    bbox: list | None = None    # active area, box-relative 0-1 (x0, y0, x1, y1)

    @property
    def active(self) -> bool:
        return self.ground >= GROUND_MIN or self.sky >= SKY_MIN

    @property
    def score(self) -> float:
        return self.ground + 4.0 * self.sky


@dataclass
class Segment:
    day: date
    samples: list[Sample]
    score: float = 0.0
    peak: int = 0               # index into samples
    bbox: list | None = None

    @property
    def start(self) -> float:
        return self.samples[0].ts

    @property
    def end(self) -> float:
        return self.samples[-1].ts


@dataclass
class Day:
    day: date
    samples: list[Sample] = field(default_factory=list)
    skyline: list[int] = field(default_factory=list)     # half-size box rows per column
    top: float | None = None                              # structure top, box-relative 0-1 (median of its columns)


def ref_frame(cfg, box) -> tuple[Path, np.ndarray]:
    """The reference everything is mapped onto: a copy (thinning deletes raw frames) of the newest midday frame,
    made once; delete it to re-pick (the box is in its pixels)."""
    p = state_dir(cfg) / "site_ref.jpg"
    if not p.is_file():
        dirs = sorted(d for d in cfg.paths.frames.iterdir() if d.is_dir() and d.name[:2] == "20")
        for d in reversed(dirs):
            rows = daylight(fr.load_day(cfg, date.fromisoformat(d.name)))
            rows = [f for f in rows if f.path.exists()]
            if len(rows) > 100:
                noon = datetime.fromisoformat(d.name).timestamp() + 13 * 3600
                shutil.copyfile(min(rows, key=lambda f: abs(f.ts - noon)).path, p)
                break
        else:
            raise ValueError("no daylight frames to use as the reference")
    return p, cv2.imread(str(p), cv2.IMREAD_COLOR)


class Scorer:
    def __init__(self, cfg, box):
        self.cfg, self.box = cfg, tuple(box)
        self.ref_p, ref = ref_frame(cfg, box)
        self.reg = Register(ref, box)
        self.ref_id = hashlib.sha1(self.ref_p.read_bytes()).hexdigest()[:10]
        x0, y0, x1, y1 = box
        self.w, self.h = (x1 - x0) // 2, (y1 - y0) // 2
        # reference full-size pixels -> half-size box
        self.S = np.array([[0.5, 0, -x0 / 2], [0, 0.5, -y0 / 2], [0, 0, 1]], np.float64)
        self.U = np.diag([2.0, 2.0, 1.0])           # half-size camera -> full-size camera
        self.debug: dict | None = None              # set to {} to keep each sample's crop and active pixels

    def cache_path(self, d: date) -> Path:
        b = "-".join(str(v) for v in self.box)
        return state_dir(self.cfg) / "site" / f"activity_{d:%Y%m%d}_{b}_{self.ref_id}.json"

    # --- geometry ---------------------------------------------------------------------------------------------------

    def strip(self, g_half: np.ndarray) -> np.ndarray:
        """The background band phase correlation aligns on (right of the box, below the skyline)."""
        h, w = g_half.shape
        return np.float32(g_half[int(h * 0.42):int(h * 0.80), self.box[2] // 2 + 20:])

    def crop(self, img_half: np.ndarray, m: np.ndarray) -> np.ndarray:
        A = self.S @ np.vstack([m, [0, 0, 1]]) @ self.U
        return cv2.warpAffine(img_half, A[:2], (self.w, self.h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    # --- a day ------------------------------------------------------------------------------------------------------

    def day(self, d: date, force: bool = False) -> Day:
        cp = self.cache_path(d)
        rows = [f for f in daylight(fr.load_day(self.cfg, d)) if f.path.exists()]
        have = read_json(cp) if cp.is_file() and not force else None
        if isinstance(have, dict) and have.get("version") == VERSION and have.get("n_rows") == len(rows):
            return Day(d, [Sample(**s) for s in have["samples"]], have["skyline"], have.get("top"))
        t0 = time.time()
        out = self._score(d, rows)
        cp.parent.mkdir(parents=True, exist_ok=True)
        write_json(cp, {"version": VERSION, "n_rows": len(rows), "skyline": out.skyline, "top": out.top,
                        "samples": [vars(s) for s in out.samples]})
        log.info("activity %s: %d samples, %d active, top %s (%.0f s)", d, len(out.samples),
                 sum(s.active for s in out.samples), out.top, time.time() - t0)
        return out

    def _score(self, d: date, rows: list[fr.Frame]) -> Day:
        sel, last = [], -1e9
        for f in rows:
            if f.ts - last >= SCORE_STEP_S:
                sel.append(f)
                last = f.ts
        if len(sel) < 10:
            return Day(d)
        # pass 1: registration (keyframes SIFT, the rest phase-correlated) and the half-size crops
        samples, crops = [], []
        twins: dict[int, np.ndarray] = {}   # keyframes: the crop through the previous keyframe's mapping
        key_ts, key_m, key_strip = -1e9, None, None

        def follow(km, kstrip, g):
            """This frame = the keyframe shifted by (dx, dy) half-size pixels: undo it, then the keyframe's mapping."""
            (dx, dy), resp = cv2.phaseCorrelate(kstrip, self.strip(g))
            if resp < 0.05:
                return None
            return (np.vstack([km, [0, 0, 1]]) @ np.array([[1, 0, -2 * dx], [0, 1, -2 * dy], [0, 0, 1]]))[:2]

        for f in sel:
            img = cv2.imread(str(f.path), cv2.IMREAD_REDUCED_COLOR_2)
            if img is None:
                continue
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            twin = None
            if f.ts - key_ts >= KEY_S or key_m is None:
                M = self.reg(cv2.imread(str(f.path), cv2.IMREAD_COLOR))
                if M is not None and self.reg.inliers >= MIN_INLIERS:
                    # a new keyframe's mapping differs a little in rotation and scale: compare this frame with the
                    # previous one through the old mapping, so the switch isn't seen as everything moving
                    old = follow(key_m, key_strip, g) if key_m is not None else None
                    twin = self.crop(img, old) if old is not None else None
                    key_ts, key_m, key_strip = f.ts, M, self.strip(g)
                    m = M
                elif key_m is None or f.ts - key_ts > 3 * KEY_S:
                    continue  # the camera is somewhere else (re-aimed): nothing to compare with
                else:
                    m = follow(key_m, key_strip, g)
            else:
                m = follow(key_m, key_strip, g)
            if m is None:
                continue
            samples.append(Sample(f.ts, f.path.relative_to(self.cfg.paths.frames).as_posix(),
                                  np.round(m, 6).tolist()))
            crops.append(self.crop(img, m))
            if twin is not None:
                twins[len(crops) - 1] = cv2.cvtColor(twin, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if len(samples) < 10:
            return Day(d)
        # the skyline from the day's median (moving clouds and vehicles average out)
        idx = np.linspace(0, len(crops) - 1, min(31, len(crops))).astype(int)
        med = np.median(np.array([crops[i] for i in idx]), axis=0).astype(np.uint8)
        sky = skyline(med)
        H, W = self.h, self.w
        rows_ = np.arange(H)[:, None]
        below = rows_ >= (sky[None, :] - 4)
        above = rows_ < (sky[None, :] - 2)
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

        def thin_dark(gray):
            return (cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, se) > 22) & above

        greys = [cv2.cvtColor(c, cv2.COLOR_BGR2GRAY).astype(np.float32) for c in crops]
        static = np.mean([thin_dark(greys[i]) for i in idx], axis=0) > 0.3    # railings, the pole: always there
        static = cv2.dilate(static.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        # pass 2: change below the skyline, thin dark lines above it
        maps = [np.zeros((H, W), bool)]
        prev = prev_g = None
        win = cv2.createHanningWindow((W, H), cv2.CV_32F) * below
        n_below = max(1, int(below.sum()))
        k3 = np.ones((3, 3), np.uint8)
        def band(gray):
            # fine detail without the pixel noise (a frame softened by the camera shaking is still alike)
            return cv2.GaussianBlur(gray, (0, 0), 1.0) - cv2.GaussianBlur(gray, (0, 0), 4)

        for i, gray in enumerate(greys):
            hp = band(gray)
            if prev is not None:
                cmp_g = twins.get(i, gray)
                hp_now = band(cmp_g) if i in twins else hp
                # what registration left between the two (sub-pixel), then a change has to survive every 1 px shift
                # of the previous frame: an edge that only moved a little isn't work
                (dx, dy), _ = cv2.phaseCorrelate(prev_g * below, cmp_g * below, win)
                p = cv2.warpAffine(prev, np.float32([[1, 0, dx], [0, 1, dy]]), (W, H), borderMode=cv2.BORDER_REFLECT)
                diff = np.full((H, W), np.inf, np.float32)
                for oy in (-1, 0, 1):
                    for ox in (-1, 0, 1):
                        diff = np.minimum(diff, np.abs(hp_now - np.roll(p, (oy, ox), axis=(0, 1))))
                # and be large next to the edges already there: an edge that got softer or sharper isn't new
                edge = cv2.dilate(np.maximum(np.abs(hp_now), np.abs(p)), k3)
                a = ((diff > GROUND_T) & (diff > 0.6 * edge)).astype(np.uint8)
                a = cv2.morphologyEx(a, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)).astype(bool) & below
                if a.sum() > 0.25 * n_below:
                    a[:] = False  # the whole view changed: a shake, focus or exposure jump, not work
                maps.append(a)
            prev, prev_g = hp, gray
        chronic = np.mean(maps, axis=0) > CHRONIC
        for s, a, gray, crop in zip(samples, maps, greys, crops):
            a = a & ~chronic
            ground, box_px = blobs(a, 6, lambda st: True)
            sk = thin_dark(gray) & ~static
            skyv, sky_px = blobs(sk, 0, lambda st: max(st[2], st[3]) >= 40 and st[4] <= 0.35 * st[2] * st[3] + 30)
            s.ground, s.sky = ground, skyv
            if self.debug is not None:
                self.debug[s.ts] = (crop, a, sk & ~static, below)
            pts = box_px + sky_px
            if pts:
                xs = [p[0] for p in pts] + [p[2] for p in pts]
                ys = [p[1] for p in pts] + [p[3] for p in pts]
                s.bbox = [round(min(xs) / W, 3), round(min(ys) / H, 3), round(max(xs) / W, 3), round(max(ys) / H, 3)]
        return Day(d, samples, [int(v) for v in sky], None)


def skyline(med: np.ndarray) -> np.ndarray:
    """Per column, the first row (from the top) with a strong colour edge in the median frame, smoothed so a stray
    cloud edge can't pull one column up."""
    lab = cv2.cvtColor(cv2.GaussianBlur(med, (0, 0), 1.2), cv2.COLOR_BGR2LAB).astype(np.float32)
    g = sum(np.abs(cv2.Sobel(lab[..., c], cv2.CV_32F, 0, 1, ksize=3)) +
            0.5 * np.abs(cv2.Sobel(lab[..., c], cv2.CV_32F, 1, 0, ksize=3)) for c in range(3))
    h, w = g.shape
    hit = g > 60
    first = np.where(hit.any(axis=0), hit.argmax(axis=0), h)
    return np.array([int(np.median(first[max(0, x - 4):x + 5])) for x in range(w)])


def blobs(a: np.ndarray, min_area: int, keep) -> tuple[int, list]:
    """Active pixels in the connected parts worth counting, and their boxes (x0, y0, x1, y1)."""
    n, _, st, _ = cv2.connectedComponentsWithStats(a.astype(np.uint8), connectivity=8)
    total, boxes = 0, []
    for k in range(1, n):
        if st[k, 4] >= min_area and keep(st[k]):
            total += int(st[k, 4])
            boxes.append((st[k, 0], st[k, 1], st[k, 0] + st[k, 2], st[k, 1] + st[k, 3]))
    return total, boxes


def structure_top(days: list[Day], cols: tuple[float, float]) -> None:
    """The structure's top each day (box-relative 0-1): the median skyline over its columns, never dropping (a
    lower reading is a measuring miss, not demolition)."""
    best = None
    for d in days:
        if not d.skyline:
            continue
        sky = np.array(d.skyline)
        a, b = int(len(sky) * cols[0]), int(len(sky) * cols[1])
        t = float(np.median(sky[a:b])) / (len(sky) * 3 / 4)   # the box is 4:3: rows = 3/4 of the columns
        best = t if best is None else min(best, t)
        d.top = round(best, 4)


def segments(days: list[Day]) -> list[Segment]:
    """Work sessions: active samples merged across gaps under MERGE_S, at least MIN_SEG_S long."""
    out = []
    for d in days:
        cur: list[Sample] = []
        last_active = None
        for s in d.samples:
            if s.active:
                if last_active is not None and s.ts - last_active.ts > MERGE_S:
                    out.append(_seg(d.day, cur))
                    cur = []
                if not cur:
                    cur = [s]
                else:
                    cur.append(s)
                last_active = s
            elif cur:
                cur.append(s)
        if cur:
            out.append(_seg(d.day, cur))
    out = [s for s in out if s is not None and s.end - s.start >= MIN_SEG_S
           and sum(x.active for x in s.samples) >= MIN_SEG_N]
    return out


def _seg(d: date, samples: list[Sample]) -> Segment | None:
    while samples and not samples[-1].active:
        samples.pop()
    if not samples:
        return None
    act = [s for s in samples if s.active]
    peak = max(range(len(samples)), key=lambda i: samples[i].score)
    # how much work: active samples weighted by their size, per minute of real time (a thinned day scores one
    # frame a minute, a raw day six)
    per_min = len(samples) / max(1.0, (samples[-1].ts - samples[0].ts) / 60)
    score = sum(min(3000.0, s.score) for s in act) / max(1.0, per_min)
    boxes = [s.bbox for s in act if s.bbox]
    bbox = [float(np.percentile([b[0] for b in boxes], 10)), float(np.percentile([b[1] for b in boxes], 10)),
            float(np.percentile([b[2] for b in boxes], 90)), float(np.percentile([b[3] for b in boxes], 90))] \
        if boxes else None
    return Segment(d, samples, round(score, 1), peak, bbox)


def scan(cfg, box, start: float, end: float, force: bool = False) -> list[Day]:
    sc = Scorer(cfg, box)
    d, last = datetime.fromtimestamp(start).date(), datetime.fromtimestamp(end).date()
    out = []
    while d <= last:
        day = sc.day(d, force=force)
        day.samples = [s for s in day.samples if start <= s.ts < end]
        out.append(day)
        d += timedelta(days=1)
    return out


def sheet(cfg, box, d: date, out: Path, n: int = 12) -> Path:
    """Debug: the day's top-scoring samples and a few ordinary ones, with what counted (red: change below the
    skyline, yellow: thin dark lines above it; the sky dimmed), plus the day's activity strip."""
    sc = Scorer(cfg, box)
    sc.debug = {}
    day = sc._score(d, [f for f in daylight(fr.load_day(cfg, d)) if f.path.exists()])
    if not day.samples:
        raise ValueError(f"nothing scored on {d}")
    by = sorted(day.samples, key=lambda s: -s.score)
    picks = by[:n * 2 // 3] + by[len(by) // 2:len(by) // 2 + n // 3]
    tiles = []
    for s in picks:
        crop, a, sk, below = sc.debug[s.ts]
        im = crop.copy()
        im[~below] = (im[~below] * 0.55).astype(np.uint8)
        im[a] = (0, 0, 255)
        im[sk] = (0, 255, 255)
        cv2.putText(im, f"{datetime.fromtimestamp(s.ts):%H:%M:%S} g{s.ground} s{s.sky}", (4, 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        tiles.append(im)
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[r:r + 4]) for r in range(0, len(tiles), 4)])
    strip = np.zeros((60, grid.shape[1], 3), np.uint8)
    t0, t1 = day.samples[0].ts, day.samples[-1].ts
    for s in day.samples:
        x = int((s.ts - t0) / max(1, t1 - t0) * (grid.shape[1] - 1))
        hgt = int(min(1.0, s.score / 2000) * 58)
        cv2.line(strip, (x, 59), (x, 59 - hgt), (0, 0, 255) if s.active else (120, 120, 120), 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), np.vstack([grid, strip]), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out
