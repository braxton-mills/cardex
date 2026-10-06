"""Custom timelapses (stdlib only): any range at a chosen speed or length, asked for from the desktop UI.

The API never writes under state\\ or the output folders (api-contract.md A.2), so it only drops a request in
ui\\render_requests\\; the supervisor picks it up, keeps the job in state\\custom\\<id>.json and runs
`render-custom --id`, which renders into <output>\\custom\\ and updates the job (progress, result).

Job: {"id", "start_ts", "end_ts", "speed" | "seconds", "created", "status", "progress", "output", "frames_used",
"frames_available", "actual_speed", "duration_s", "reason", "finished"}; status is queued | rendering | ok |
skipped | failed.
"""
from __future__ import annotations

import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from .config import read_json, write_json

MAX_RANGE_S = 7 * 86400
MIN_OUT_S, MAX_OUT_S = 2, 600
ID_RE = re.compile(r"^[0-9a-f]{8}$")
CUSTOM_RE = re.compile(r"^campi_custom_\d{8}_\d{4}-\d{8}_\d{4}_([0-9a-f]{8})\.mp4$")
ACTIVE = ("queued", "rendering")


def new_id() -> str:
    return secrets.token_hex(4)


def requests_dir(cfg) -> Path:
    return cfg.paths.ui / "render_requests"


def jobs_dir(cfg) -> Path:
    return cfg.paths.state / "custom"


def job_path(cfg, job_id: str) -> Path:
    return jobs_dir(cfg) / f"{job_id}.json"


def read_job(cfg, job_id: str) -> dict | None:
    j = read_json(job_path(cfg, job_id)) if ID_RE.match(job_id or "") else None
    return j if isinstance(j, dict) and j.get("id") == job_id else None


def write_job(cfg, job: dict) -> None:
    jobs_dir(cfg).mkdir(exist_ok=True)
    write_json(job_path(cfg, job["id"]), job)


def jobs(cfg) -> list[dict]:
    """Every job, newest first."""
    d = jobs_dir(cfg)
    out = [j for p in d.glob("*.json") if (j := read_job(cfg, p.stem))] if d.is_dir() else []
    return sorted(out, key=lambda j: j.get("created", 0), reverse=True)


def out_seconds(job: dict) -> float:
    if job.get("seconds"):
        return float(job["seconds"])
    return (job["end_ts"] - job["start_ts"]) / float(job["speed"])


def target_frames(job: dict, base_fps: int) -> int:
    return max(2, round(out_seconds(job) * base_fps))


def file_name(job: dict) -> str:
    s, e = (datetime.fromtimestamp(job[k]) for k in ("start_ts", "end_ts"))
    return f"campi_custom_{s:%Y%m%d_%H%M}-{e:%Y%m%d_%H%M}_{job['id']}.mp4"


def validate(start_ts: float, end_ts: float, speed: float | None, seconds: float | None,
             now: float | None = None) -> str | None:
    """None if the request is fine, else what's wrong (shown to the user)."""
    now = now or time.time()
    if (speed is None) == (seconds is None):
        return "give either a speed or a length"
    if end_ts <= start_ts:
        return "the end must be after the start"
    if end_ts > now + 60:
        return "the end can't be in the future"
    span = end_ts - start_ts
    if span > MAX_RANGE_S:
        return "the range can be at most 7 days"
    if speed is not None:
        if speed < 1:
            return "speed must be at least 1x"
        secs = span / speed
        if secs > MAX_OUT_S:
            return (f"that would be {secs / 60:.0f} min long; the most is {MAX_OUT_S // 60} min "
                    f"(use {span / MAX_OUT_S:.0f}x or faster)")
        if secs < MIN_OUT_S:
            return f"that would be under {MIN_OUT_S} s long; use {span / MIN_OUT_S:.0f}x or slower"
    elif not MIN_OUT_S <= seconds <= MAX_OUT_S:
        return f"length must be {MIN_OUT_S}-{MAX_OUT_S} s"
    return None


def new_job(start_ts: float, end_ts: float, speed: float | None, seconds: float | None) -> dict:
    return {"id": new_id(), "start_ts": round(start_ts, 3), "end_ts": round(end_ts, 3), "speed": speed,
            "seconds": seconds, "created": round(time.time(), 3), "status": "queued", "progress": 0.0}
