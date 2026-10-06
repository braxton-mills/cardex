"""Frame index (one index.csv per day), selection, night filtering and gap segmentation."""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

INDEX_HEADER = "ts,file,luma,sha1,bytes,exposure"  # exposure: seconds x gain, from the Pi (blank = unknown)


@dataclass
class Frame:
    ts: float
    path: Path
    luma: float
    exposure: float | None = None  # seconds x analogue gain


def row_exposure(row: dict) -> str:
    """The exposure field of an index row; rows appended under an older 5-column header carry it as the extra field."""
    return row.get("exposure") or (row.get(None) or [""])[0] or ""


def day_dir(cfg, d: date) -> Path:
    return cfg.paths.frames / d.isoformat()


def load_day(cfg, d: date) -> list[Frame]:
    ddir = day_dir(cfg, d)
    idx = ddir / "index.csv"
    if not idx.exists():
        return []
    out = []
    with open(idx, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                exp = row_exposure(row)
                out.append(Frame(float(row["ts"]), ddir / row["file"], float(row["luma"]), float(exp) if exp else None))
            except (KeyError, TypeError, ValueError):
                continue  # partially written last line
    return out


def frames_between(cfg, start_ts: float, end_ts: float) -> list[Frame]:
    d = datetime.fromtimestamp(start_ts).date()
    last = datetime.fromtimestamp(end_ts).date()
    out = []
    while d <= last:
        out += [f for f in load_day(cfg, d) if start_ts <= f.ts < end_ts]
        d += timedelta(days=1)
    out.sort(key=lambda f: f.ts)
    return [f for f in out if f.path.exists()]


def usable(cfg, frames: list[Frame]) -> list[Frame]:
    """Frames the renders use: all of them, night included, unless [render] min_luma drops near-black ones."""
    min_luma = float(getattr(cfg.render, "min_luma", 0) or 0)
    return [f for f in frames if f.luma >= min_luma] if min_luma > 0 else frames


def sample_evenly(frames: list[Frame], n: int) -> list[Frame]:
    if len(frames) <= n:
        return frames
    step = (len(frames) - 1) / (n - 1)
    return [frames[round(i * step)] for i in range(n)]


def is_gap(delta: float, max_gap_s: float, ratio: float = 0.0, *around: list[float]) -> bool:
    """A capture gap: further apart than max_gap_s and, when `ratio` is set, more than ratio x the median spacing on
    each side given in `around` (the spacings just before, and just after when known). Long night exposures stretch
    the cadence at dusk (2 -> 4 -> 8 s); that is not a gap."""
    if delta <= max_gap_s:
        return False
    return not (ratio > 0 and any(s and delta <= ratio * statistics.median(s) for s in around))


def segment(frames: list[Frame], max_gap_s: float, ratio: float = 0.0) -> list[list[Frame]]:
    """Split at capture gaps (see is_gap; each spacing is judged against the 5 before and the 5 after it)."""
    d = [b.ts - a.ts for a, b in zip(frames, frames[1:])]
    segs: list[list[Frame]] = []
    for i, f in enumerate(frames):
        if i and not is_gap(d[i - 1], max_gap_s, ratio, d[max(0, i - 6):i - 1], d[i:i + 5]):
            segs[-1].append(f)
        else:
            segs.append([f])
    return segs
