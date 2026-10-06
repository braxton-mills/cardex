"""Frame index (one index.csv per day), selection, night filtering and gap segmentation."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

INDEX_HEADER = "ts,file,luma,sha1,bytes"


@dataclass
class Frame:
    ts: float
    path: Path
    luma: float


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
                out.append(Frame(float(row["ts"]), ddir / row["file"], float(row["luma"])))
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


def segment(frames: list[Frame], max_gap_s: float) -> list[list[Frame]]:
    """Split wherever consecutive frames are further apart than max_gap_s (capture gaps, night)."""
    segs: list[list[Frame]] = []
    for f in frames:
        if segs and f.ts - segs[-1][-1].ts <= max_gap_s:
            segs[-1].append(f)
        else:
            segs.append([f])
    return segs
