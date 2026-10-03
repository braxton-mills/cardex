"""Timelapse files for the UI: 10-minute clips, daily videos, archive parts, and seek (wall-clock time -> position in a
video). Read-only: lists files and reads the frame index; never opens a video except through ffprobe for its length."""
from __future__ import annotations

import bisect
import json
import math
import re
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from .. import frames as fr
from ..archive import CLIP_RE, clip_start
from ..config import NO_WINDOW, read_json
from ..sightings_db import utc_iso

DAILY_RE = re.compile(r"^campi_daily_(\d{4}-\d{2}-\d{2})\.mp4$")
ARCHIVE_RE = re.compile(r"^campi_archive_(\d{3})\.mp4$")
NEAREST_MAX_S = 600  # a daily-video seek further than this from any usable frame is "not in the video"


def media_url(root: str, rel: str) -> str:
    return f"/media/{root}/{quote(rel)}"


def local_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class Library:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self._durations: dict[tuple, float | None] = {}

    # ------------------------------------------------------------ listings

    def current_part(self) -> int:
        """The archive part the service appends to next (rewritten on every append: never served)."""
        return int((read_json(self.cfg.paths.state / "archive.json", {}) or {}).get("part", 1))

    def exact_starts(self) -> dict[str, float]:
        """Window starts with seconds for clips the state files name (render-now / test clips aren't on the grid)."""
        out = {}
        latest = read_json(self.cfg.paths.state / "latest.json", {}) or {}
        if latest.get("clip") and latest.get("window_start_ts"):
            out[Path(latest["clip"]).name] = float(latest["window_start_ts"])
        last = read_json(self.cfg.paths.state / "last_clip.json", {}) or {}
        if last.get("out") and last.get("window_start"):
            try:
                out.setdefault(Path(last["out"]).name, datetime.fromisoformat(last["window_start"]).timestamp())
            except ValueError:
                pass
        return out

    def clip_files(self) -> list[tuple[Path, float]]:
        """(path, window start) of every finished 10-minute clip, oldest first. latest.mp4 and .part files never
        match CLIP_RE."""
        exact = self.exact_starts()
        out = []
        for p in self.cfg.paths.out.glob("campi_*.mp4"):
            if CLIP_RE.match(p.name):
                out.append((p, exact.get(p.name) or clip_start(p)))
        return sorted(out, key=lambda x: (x[1], x[0].name))

    def clip_info(self, p: Path, start: float, stars: dict, counts=None) -> dict | None:
        try:
            stt = p.stat()
        except OSError:
            return None  # expired between listing and stat
        step, window = self.cfg.render.interval_min * 60, self.cfg.render.window_min * 60
        if start % step:  # render-now / `campi test` clip: its window may be shorter (campi test = 1 minute)
            dur = self.duration(p)
            if dur:  # each captured frame is 1/base_fps s of video (night frames dropped: an upper bound)
                window = min(window, math.ceil(dur * self.cfg.output.base_fps * self.cfg.capture.interval_s / 60) * 60)
        return {"name": p.name, "start_at": utc_iso(start), "start_local": local_iso(start),
                "end_at": utc_iso(start + window), "end_local": local_iso(start + window),
                "on_grid": start % step == 0, "size": stt.st_size, "modified_at": utc_iso(stt.st_mtime),
                "modified_local": local_iso(stt.st_mtime),
                "expires_at": utc_iso(stt.st_mtime + self.cfg.retention.clips_hours * 3600),
                "url": media_url("clips", p.name), "starred": p.name in stars,
                "sightings": counts(start, start + window) if counts else None}

    def clips(self, hours: float = 24, counts=None) -> list[dict]:
        """Clips whose window started in the last `hours`, newest first."""
        stars = self.store.stars("clip")
        lo = time.time() - hours * 3600
        out = [self.clip_info(p, s, stars, counts) for p, s in self.clip_files() if s >= lo]
        return [c for c in reversed(out) if c]

    def newest_clip(self, counts=None) -> dict | None:
        """The dated clip latest.mp4 is a copy of (latest.mp4 itself is never served)."""
        latest = read_json(self.cfg.paths.state / "latest.json", {}) or {}
        files = self.clip_files()
        named = [(p, s) for p, s in files if latest.get("clip") and p.name == Path(latest["clip"]).name]
        for p, s in (named or files[::-1]):
            info = self.clip_info(p, s, self.store.stars("clip"), counts)
            if info:
                return info
        return None

    def daily_files(self) -> list[tuple[Path, date]]:
        out = []
        for p in self.cfg.paths.daily.glob("campi_daily_*.mp4"):
            m = DAILY_RE.match(p.name)
            if m:
                out.append((p, date.fromisoformat(m.group(1))))
        return sorted(out, key=lambda x: x[1])

    def daily(self) -> list[dict]:
        stars = self.store.stars("daily")
        out = []
        for p, d in reversed(self.daily_files()):
            try:
                stt = p.stat()
            except OSError:
                continue
            out.append({"day": d.isoformat(), "name": p.name, "size": stt.st_size,
                        "modified_at": utc_iso(stt.st_mtime), "modified_local": local_iso(stt.st_mtime),
                        "url": media_url("daily", p.name),
                        "starred": p.name in stars})
        return out

    def archive(self) -> list[dict]:
        cur = self.current_part()
        out = []
        for p in sorted(self.cfg.paths.archive.glob("campi_archive_*.mp4")):
            m = ARCHIVE_RE.match(p.name)
            if not m:
                continue  # campi_archive_001.tmp.mp4 while an append is running
            try:
                stt = p.stat()
            except OSError:
                continue
            n = int(m.group(1))
            out.append({"part": n, "name": p.name, "size": stt.st_size, "modified_at": utc_iso(stt.st_mtime),
                        "modified_local": local_iso(stt.st_mtime),
                        "current": n == cur, "url": None if n == cur else media_url("archive", p.name)})
        return out[::-1]

    # ------------------------------------------------------------ seek

    def duration(self, p: Path) -> float | None:
        """Video length via ffprobe, cached per (name, size, mtime)."""
        try:
            stt = p.stat()
        except OSError:
            return None
        key = (p.name, stt.st_size, stt.st_mtime_ns)
        if key not in self._durations:
            try:
                out = subprocess.run([self.cfg.tools.ffprobe, "-v", "error", "-show_entries", "format=duration",
                                      "-of", "json", str(p)], capture_output=True, text=True, timeout=30,
                                     creationflags=NO_WINDOW).stdout
                self._durations[key] = float(json.loads(out or "{}").get("format", {}).get("duration") or 0) or None
            except (OSError, ValueError, subprocess.SubprocessError):
                self._durations[key] = None
        return self._durations[key]

    @staticmethod
    def _nearest(frames: list, ts: float) -> int:
        """Index of the usable frame closest to ts."""
        i = bisect.bisect_left([f.ts for f in frames], ts)
        if i >= len(frames) or (i > 0 and ts - frames[i - 1].ts < frames[i].ts - ts):
            i -= 1
        return max(0, i)

    def seek(self, ts: float) -> dict | None:
        """Where wall-clock time ts is in the timelapse.

        10-minute clip covering ts: render_frames turns each usable frame of the window into interp_factor frames at
        base_fps * interp_factor, so usable frame i starts at i / base_fps seconds. Exact while the clip exists (its
        raw frames are kept longer than the clip). Otherwise the daily video, proportionally: approximate."""
        cfg = self.cfg
        fps = float(cfg.output.base_fps)
        step, window = cfg.render.interval_min * 60, cfg.render.window_min * 60
        cands = [(p, s) for p, s in self.clip_files() if s <= ts < s + window]
        cands.sort(key=lambda x: (x[1] % step == 0, x[1]), reverse=True)  # on-grid first, then the latest start
        for p, start in cands:
            frames = fr.usable(cfg, fr.frames_between(cfg, start, start + window))
            if not frames:
                continue
            dur = self.duration(p)
            if dur is None or dur < len(frames) / fps - 1.0:
                continue  # shorter than a full window (campi test) or unreadable
            i = self._nearest(frames, ts)
            off = i / fps
            return {"kind": "clip", "name": p.name, "url": media_url("clips", p.name), "offset_s": round(off, 3),
                    "duration_s": round(dur, 3), "fraction": round(off / dur, 5) if dur else 0,
                    "frame_at": utc_iso(frames[i].ts), "frame_local": local_iso(frames[i].ts),
                    "approximate": False}
        day = datetime.fromtimestamp(ts).date()
        p = cfg.paths.daily / f"campi_daily_{day.isoformat()}.mp4"
        if not p.is_file():
            return None
        frames = fr.usable(cfg, sorted((f for f in fr.load_day(cfg, day) if f.path.exists()), key=lambda f: f.ts))
        if not frames:
            return None
        i = self._nearest(frames, ts)
        if abs(frames[i].ts - ts) > NEAREST_MAX_S:
            return None  # night, or before capture started that day: nothing near that time in the video
        frac = i / len(frames)
        dur = self.duration(p) or min(len(frames), int(cfg.daily.target_seconds * fps)) / fps
        return {"kind": "daily", "name": p.name, "url": media_url("daily", p.name),
                "offset_s": round(frac * dur, 3), "duration_s": round(dur, 3), "fraction": round(frac, 5),
                "frame_at": utc_iso(frames[i].ts), "frame_local": local_iso(frames[i].ts), "approximate": True}
