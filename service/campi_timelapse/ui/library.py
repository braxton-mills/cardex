"""Timelapse files in the contract's shapes (§4.8-4.11, §5.4-5.5, §5.8, §6.4, §6.5): 10-minute clips, daily
videos, archive parts, and seek (instant -> position in a video). Read-only: lists files, reads the render index
and the frame index, and reads the first few KB of an MP4 for its duration."""
from __future__ import annotations

import bisect
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import custom_jobs as cj
from .. import frames as fr
from .. import render_index
from ..archive import CLIP_RE, clip_start
from ..config import read_json
from . import mp4
from .contract import invalid, iso, local_day, midnight, not_found, page
from .media import ARCHIVE_RE, DAILY_RE

SEEK_REASONS = ("pending_render", "not_rendered", "no_frames", "expired")


def clip_id(name: str) -> str:
    return name[len("campi_"):-len(".mp4")]


class Library:
    def __init__(self, cfg, store, sightings):
        self.cfg, self.store, self.sd = cfg, store, sightings
        self._days: dict[date, tuple] = {}
        self._days_lock = threading.Lock()

    # ------------------------------------------------------------ helpers

    def _stat(self, p: Path):
        try:
            return p.stat()
        except OSError:
            return None

    def _duration(self, p: Path, stt) -> float | None:
        return mp4.duration(p, stt.st_size, stt.st_mtime_ns)

    def aligned(self, ts: float) -> bool:
        """On the supervisor's grid: local midnight + k * interval_min."""
        step = int(self.cfg.render.interval_min) * 60
        return abs((ts - midnight(local_day(ts))) % step) < 0.5

    def grid_window(self, ts: float) -> tuple[float, float]:
        """The scheduled window containing ts: it ends on the next boundary (like Supervisor.next_boundary)."""
        step = int(self.cfg.render.interval_min) * 60
        m = midnight(local_day(ts))
        end = m + ((ts - m) // step + 1) * step
        return end - int(self.cfg.render.window_min) * 60, end

    def usable_day(self, d: date) -> list[float] | None:
        """Sorted timestamps of usable frames in that day's index.csv; None if the day has no index. Cached on the
        index file's size and mtime."""
        idx = fr.day_dir(self.cfg, d) / "index.csv"
        stt = self._stat(idx)
        if stt is None:
            return None
        key = (stt.st_size, stt.st_mtime_ns)
        with self._days_lock:
            hit = self._days.get(d)
            if hit and hit[0] == key:
                return hit[1]
        ts = sorted(f.ts for f in fr.usable(self.cfg, fr.load_day(self.cfg, d)))
        with self._days_lock:
            if len(self._days) > 8:
                self._days.clear()
            self._days[d] = (key, ts)
        return ts

    # ------------------------------------------------------------ clips

    def clip_files(self) -> list[dict]:
        """Existing clips (CLIP_RE only: never latest.mp4, .part or .tmp files) with their windows."""
        out = []
        win = int(self.cfg.render.window_min) * 60
        for p in self.cfg.paths.out.glob("campi_*.mp4"):
            if not CLIP_RE.match(p.name):
                continue
            stt = self._stat(p)
            if stt is None:
                continue
            idx = render_index.read(self.cfg, p.name)
            if idx and idx.get("output") == p.name:
                ws, we, exact = float(idx["window_start_ts"]), float(idx["window_end_ts"]), True
            else:
                ws = clip_start(p)
                we, exact, idx = ws + win, False, None
            out.append({"id": clip_id(p.name), "path": p, "stat": stt, "start": ws, "end": we, "exact": exact,
                        "index": idx})
        out.sort(key=lambda c: (c["start"], c["id"]), reverse=True)
        return out

    def clip_json(self, c: dict, signer, stars: dict | None = None) -> dict:
        stars = self.store.stars("clip") if stars is None else stars
        stt = c["stat"]
        return {
            "id": c["id"], "day": local_day(c["start"]).isoformat(), "window_start": iso(c["start"]),
            "window_end": iso(c["end"]), "exact_window": c["exact"], "duration_s": self._duration(c["path"], stt),
            "size_bytes": stt.st_size, "modified_at": iso(stt.st_mtime),
            "expires_after": iso(stt.st_mtime + float(self.cfg.retention.clips_hours) * 3600),
            "sightings_count": self.sd.count_between(c["start"], c["end"]),
            "starred": c["id"] in stars,
            "media": {"video": signer(f"/media/clips/{c['path'].name}"),
                      "poster": signer(f"/media/posters/clips/{c['id']}.jpg")},
        }

    def latest_id(self) -> str | None:
        latest = read_json(self.cfg.paths.state / "latest.json", {}) or {}
        name = Path(latest.get("clip") or "").name
        if CLIP_RE.match(name) and (self.cfg.paths.out / name).is_file():
            return clip_id(name)
        return None

    def clips(self, signer) -> dict:
        stars = self.store.stars("clip")
        files = self.clip_files()
        ids = {c["id"] for c in files}
        latest = self.latest_id()
        return {"latest_id": latest if latest in ids else None,
                "retention_hours": int(self.cfg.retention.clips_hours), "window_min": int(self.cfg.render.window_min),
                "items": [self.clip_json(c, signer, stars) for c in files]}

    def find_clip(self, cid: str) -> dict | None:
        return next((c for c in self.clip_files() if c["id"] == cid), None)

    def clip(self, cid: str, signer) -> dict:
        c = self.find_clip(cid)
        if c is None:
            raise not_found(f"no clip {cid}")
        return self.clip_json(c, signer)

    def newest_clip(self, signer) -> dict | None:
        files = self.clip_files()
        latest = self.latest_id()
        c = next((c for c in files if c["id"] == latest), files[0] if files else None)
        return self.clip_json(c, signer) if c else None

    # ------------------------------------------------------------ daily videos

    def daily_files(self) -> list[tuple[Path, date, object]]:
        out = []
        for p in self.cfg.paths.daily.glob("campi_daily_*.mp4"):
            m = DAILY_RE.match(p.name)
            stt = self._stat(p) if m else None
            if stt is not None:
                out.append((p, date.fromisoformat(m.group(1)), stt))
        return sorted(out, key=lambda x: x[1], reverse=True)

    def daily_json(self, p: Path, d: date, stt, signer, stars: dict | None = None) -> dict:
        stars = self.store.stars("daily") if stars is None else stars
        return {"day": d.isoformat(), "duration_s": self._duration(p, stt), "size_bytes": stt.st_size,
                "modified_at": iso(stt.st_mtime),
                "sightings_count": self.sd.count_between(midnight(d), midnight(d + timedelta(days=1))),
                "starred": d.isoformat() in stars,
                "media": {"video": signer(f"/media/daily/{p.name}"),
                          "poster": signer(f"/media/posters/daily/{d.isoformat()}.jpg")}}

    def daily_page(self, q, signer) -> dict:
        files = self.daily_files()
        pg = page(files, lambda x: [x[1].isoformat()], q)
        stars = self.store.stars("daily")
        return {"items": [self.daily_json(p, d, stt, signer, stars) for p, d, stt in pg["items"]],
                "next_cursor": pg["next_cursor"], "total": pg["total"]}

    def find_daily(self, day: str):
        try:
            d = date.fromisoformat(day)
        except ValueError:
            return None
        return next((x for x in self.daily_files() if x[1] == d), None)

    def daily(self, day: str, signer) -> dict:
        x = self.find_daily(day)
        if x is None:
            raise not_found(f"no daily video for {day}")
        return self.daily_json(*x, signer)

    # ------------------------------------------------------------ archive

    def current_part(self) -> int:
        """§6.5: the higher of archive.json's part and the highest part on disk (append_pending creates part N+1
        before it updates archive.json)."""
        st = read_json(self.cfg.paths.state / "archive.json", {}) or {}
        try:
            cur = int(st.get("part", 1))
        except (TypeError, ValueError):
            cur = 1
        for p in self.cfg.paths.archive.glob("campi_archive_*.mp4"):
            m = ARCHIVE_RE.match(p.name)
            if m:
                cur = max(cur, int(m.group(1)))
        return cur

    def archive(self, signer) -> dict:
        cur = self.current_part()
        items = []
        for p in self.cfg.paths.archive.glob("campi_archive_*.mp4"):
            m = ARCHIVE_RE.match(p.name)  # never campi_archive_001.tmp.mp4 (an append in progress)
            stt = self._stat(p) if m else None
            if stt is None:
                continue
            n = int(m.group(1))
            current = n == cur
            items.append({"part": n, "size_bytes": stt.st_size, "modified_at": iso(stt.st_mtime),
                          "duration_s": None if current else self._duration(p, stt), "current": current,
                          "media": {"video": None if current else signer(f"/media/archive/{p.name}")}})
        items.sort(key=lambda a: a["part"], reverse=True)
        return {"enabled": bool(self.cfg.archive.enabled), "items": items}

    # ------------------------------------------------------------ custom timelapses (desktop only)

    def custom(self, signer) -> dict:
        """Jobs the supervisor knows about plus requests it hasn't picked up yet (shown as queued); a job with a
        pending delete is already gone as far as the UI is concerned."""
        reqs = [r for p in sorted(cj.requests_dir(self.cfg).glob("*.json")) if isinstance(r := read_json(p), dict)]             if cj.requests_dir(self.cfg).is_dir() else []
        deleting = {r.get("id") for r in reqs if r.get("action") == "delete"}
        jobs = cj.jobs(self.cfg)
        known = {j["id"] for j in jobs}
        jobs += [{**r, "status": "queued", "progress": 0.0} for r in reqs
                 if r.get("action") == "render" and r.get("id") not in known]
        stars = self.store.stars("custom")
        items = []
        for j in sorted(jobs, key=lambda j: j.get("created", 0), reverse=True):
            if j["id"] in deleting:
                continue
            item = {k: j.get(k) for k in ("id", "status", "progress", "speed", "seconds", "frames_used",
                                          "frames_available", "actual_speed", "duration_s", "reason")}
            item.update(window_start=iso(j["start_ts"]), window_end=iso(j["end_ts"]), created=iso(j.get("created")),
                        started=iso(j.get("started")), finished=iso(j.get("finished")), starred=j["id"] in stars, file=None, size_bytes=None,
                        media={"video": None})
            if j.get("status") == "ok":
                stt = self._stat(self.cfg.paths.custom / str(j.get("output")))
                if stt is None:
                    continue  # deleted by hand
                item.update(file=j["output"], size_bytes=stt.st_size,
                            media={"video": signer(f"/media/custom/{j['output']}")})
            items.append(item)
        st = read_json(self.cfg.paths.state / "status.json", {}) or {}
        return {"items": items, "service_running": time.time() - (st.get("updated") or 0) < 30,
                "renders_deferred": bool(st.get("renders_deferred"))}

    def custom_exists(self, job_id: str) -> bool:
        return any(i["id"] == job_id for i in self.custom(lambda p: p)["items"])

    # ------------------------------------------------------------ seek (§6.4)

    def seek(self, ts: float, signer) -> dict:
        now = time.time()
        if ts > now + 1:
            raise invalid("ts is in the future")
        cfg = self.cfg
        win = int(cfg.render.window_min) * 60
        base = {"ts": iso(ts), "target": "none", "clip_id": None, "day": None, "video": None, "offset_s": None,
                "approximate": False, "reason": None}

        # 0. capture gap: that day's frame index exists but nothing usable near ts
        days = {local_day(ts - win), local_day(ts), local_day(ts + win)}
        indexed = {d: self.usable_day(d) for d in days}
        if indexed.get(local_day(ts)) is not None:
            near = [t for d in days for t in (indexed[d] or []) if abs(t - ts) <= win]
            if not near:
                return {**base, "reason": "no_frames"}

        # 1. a clip whose window contains ts: exact window first, then on the grid, then the newest file
        cands = [c for c in self.clip_files() if c["start"] <= ts < c["end"]]
        cands.sort(key=lambda c: (c["exact"], self.aligned(c["start"]), c["stat"].st_mtime), reverse=True)
        for c in cands:
            if c["index"]:
                k = bisect.bisect_left(c["index"]["frame_ts"], ts)
                fps = float(c["index"].get("base_fps") or cfg.output.base_fps)
            else:
                k = len(fr.usable(cfg, fr.frames_between(cfg, c["start"], ts)))
                fps = float(cfg.output.base_fps)
            dur = self._duration(c["path"], c["stat"])
            return {**base, "target": "clip", "clip_id": c["id"], "video": signer(f"/media/clips/{c['path'].name}"),
                    "offset_s": self._clamp(k / fps, dur)}

        # 2. that day's daily video
        d = local_day(ts)
        x = self.find_daily(d.isoformat())
        if x is not None:
            p, _, stt = x
            dur = self._duration(p, stt)
            idx = render_index.read(cfg, p.name)
            approx = False
            if idx and idx.get("output") == p.name:
                off = bisect.bisect_left(idx["frame_ts"], ts) / float(idx.get("base_fps") or cfg.output.base_fps)
            else:
                minutes = sorted({int(t // 60) for t in (indexed.get(d) or [])})
                if minutes and dur:
                    off = dur * bisect.bisect_left(minutes, int(ts // 60)) / len(minutes)
                else:
                    off = 0.0
                approx = True
            return {**base, "target": "daily", "day": d.isoformat(), "video": signer(f"/media/daily/{p.name}"),
                    "offset_s": self._clamp(off, dur), "approximate": approx}

        # 3. nothing to show: why
        return {**base, "reason": self._why_not(ts, now)}

    @staticmethod
    def _clamp(off: float, dur: float | None) -> float:
        if dur:
            off = min(off, max(0.0, dur - 0.05))
        return round(max(0.0, off), 3)

    def _why_not(self, ts: float, now: float) -> str:
        cfg = self.cfg
        ws, we = self.grid_window(ts)
        if now < we + float(cfg.render.delay_s) + float(cfg.render.timeout_min) * 60:
            return "pending_render"
        queue = read_json(cfg.paths.state / "render_queue.json", []) or []
        if any(isinstance(b, (int, float)) and abs(b - we) < 1 for b in queue):
            return "pending_render"
        st = read_json(cfg.paths.state / "status.json", {}) or {}
        if st.get("supervisor_pid") and now - st.get("updated", 0) < 30 and st.get("clip_running"):
            last = read_json(cfg.paths.state / "latest.json", {}) or {}
            if ts >= float(last.get("window_start_ts") or 0) + int(cfg.render.window_min) * 60:
                return "pending_render"  # a render is running and ts is newer than the last published clip
        if ts < now - float(cfg.retention.clips_hours) * 3600:
            return "expired"
        return "not_rendered"


def parse_clip_id(cid: str) -> float | None:
    """Window start (epoch) from a clip id; None if it isn't one."""
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})", cid)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2) + m.group(3), "%Y-%m-%d%H%M").timestamp()
    except ValueError:
        return None

