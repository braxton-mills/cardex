"""Render index (stdlib only): state\\render_index\\<output stem>.json next to every rendered clip / daily video.

Written by render after the video is replaced, deleted by housekeeping with its video, read by the UI/API. It holds
the real window and the timestamps of the frames actually encoded, so seeking into a video is exact:
{"output", "window_start_ts", "window_end_ts", "base_fps", "interp_factor", "frame_ts": [...]}.
"""
from __future__ import annotations

from pathlib import Path

from .config import read_json, write_json


def index_dir(cfg) -> Path:
    return cfg.paths.state / "render_index"


def index_path(cfg, video_name: str) -> Path:
    return index_dir(cfg) / f"{Path(video_name).stem}.json"


def write(cfg, out: Path, start_ts: float, end_ts: float, frame_ts: list[float]) -> None:
    index_dir(cfg).mkdir(exist_ok=True)
    write_json(index_path(cfg, out.name), {
        "output": out.name, "window_start_ts": start_ts, "window_end_ts": end_ts,
        "base_fps": int(cfg.output.base_fps), "interp_factor": int(cfg.output.interp_factor),
        "frame_ts": [round(t, 3) for t in frame_ts]})


def read(cfg, video_name: str) -> dict | None:
    d = read_json(index_path(cfg, video_name))
    if not isinstance(d, dict) or not isinstance(d.get("frame_ts"), list):
        return None
    return d


def delete(cfg, video_name: str) -> None:
    index_path(cfg, video_name).unlink(missing_ok=True)
