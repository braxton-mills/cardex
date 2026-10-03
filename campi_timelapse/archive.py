"""Rolling archive: append each finished 10-minute clip onto one long MP4, starting a new part at max_gb.

The originals stay untouched. Appending is a stream copy (no re-encode): ffmpeg's concat demuxer writes
old archive + new clips to a temp file, which then replaces the archive. If the archive is open in a player
(locked on Windows), the clips stay pending and are appended on the next run.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .config import NO_WINDOW, FileLock, read_json, write_json

log = logging.getLogger("archive")

CLIP_RE = re.compile(r"^campi_(\d{4}-\d{2}-\d{2})_(\d{4})\.mp4$")


def clip_start(p: Path) -> float | None:
    m = CLIP_RE.match(p.name)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y-%m-%d%H%M").timestamp()


def stream_params(cfg, p: Path) -> dict:
    out = subprocess.run([cfg.tools.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,profile,width,height,r_frame_rate,pix_fmt:format=duration",
                          "-of", "default=noprint_wrappers=1", str(p)],
                         capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW).stdout
    d = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    d["duration"] = float(d.get("duration", 0) or 0)
    return d


def _key(params: dict) -> tuple:
    return tuple(params.get(k) for k in ("codec_name", "profile", "width", "height", "r_frame_rate", "pix_fmt"))


def part_path(cfg, n: int) -> Path:
    return cfg.paths.archive / f"campi_archive_{n:03d}.mp4"


def _write(cfg, inputs: list[Path], dest: Path) -> float:
    """dest = concatenation of inputs (stream copy). Returns the new duration."""
    tmp = dest.with_name(dest.stem + ".tmp.mp4")
    lst = cfg.paths.work / "archive_concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in inputs), encoding="utf-8")
    cmd = [cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, creationflags=NO_WINDOW)
    lst.unlink(missing_ok=True)
    expected = sum(stream_params(cfg, p)["duration"] for p in inputs)
    got = stream_params(cfg, tmp)["duration"] if tmp.exists() else 0
    if r.returncode != 0 or abs(got - expected) > 1.0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"concat failed (rc {r.returncode}, {got:.1f}s of {expected:.1f}s): {r.stderr[-500:]}")
    try:
        os.replace(tmp, dest)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise
    return got


def append_pending(cfg) -> dict:
    """Append every clip newer than the last archived one, in order, splitting parts at max_gb."""
    a = cfg.archive
    if not a.enabled:
        return {"status": "disabled"}
    lock = FileLock(cfg.paths.state / "archive.lock")
    if not lock.acquire():
        return {"status": "busy"}
    try:
        state_path = cfg.paths.state / "archive.json"
        st = read_json(state_path, {}) or {}
        part = int(st.get("part", 1))
        last_ts = float(st.get("last_window_ts", 0))
        limit = int(a.max_gb * 1e9)

        clips = sorted((c for c in cfg.paths.out.glob("campi_*.mp4") if (clip_start(c) or 0) > last_ts),
                       key=clip_start)
        if not clips:
            return {"status": "up to date", "part": str(part_path(cfg, part))}

        # Group pending clips into batches: same part as long as size and stream format allow.
        dest = part_path(cfg, part)
        size = dest.stat().st_size if dest.exists() else 0
        fmt = tuple(st["format"]) if st.get("format") else None
        batches: list[tuple[int, list[Path]]] = []
        for c in clips:
            k = _key(stream_params(cfg, c))
            csize = c.stat().st_size
            if size and (size + csize > limit or (fmt and k != fmt)):
                part, size = part + 1, 0
                log.info("starting archive part %d (%s)", part,
                         "size limit" if fmt is None or k == fmt else "stream format changed")
            if not batches or batches[-1][0] != part:
                batches.append((part, []))
            batches[-1][1].append(c)
            size += csize
            fmt = k

        done = []
        for n, batch in batches:
            dest = part_path(cfg, n)
            inputs = ([dest] if dest.exists() else []) + batch
            try:
                if len(inputs) == 1:
                    shutil.copyfile(batch[0], dest.with_name(dest.stem + ".tmp.mp4"))
                    os.replace(dest.with_name(dest.stem + ".tmp.mp4"), dest)
                    dur = stream_params(cfg, dest)["duration"]
                else:
                    dur = _write(cfg, inputs, dest)
            except PermissionError:
                log.warning("%s is open in another program; %d clip(s) will be appended next time",
                            dest.name, sum(len(b) for _, b in batches[len(done):]))
                break
            st.update(part=n, last_clip=batch[-1].name, last_window_ts=clip_start(batch[-1]),
                      format=list(_key(stream_params(cfg, batch[-1]))))
            write_json(state_path, st)
            done.append((n, len(batch), dur))
            log.info("appended %d clip(s) to %s -> %.1f MB, %.0fs", len(batch), dest.name,
                     dest.stat().st_size / 1e6, dur)
        return {"status": "ok" if done else "pending", "appended": [
            {"part": str(part_path(cfg, n)), "clips": k, "duration_s": round(d, 1)} for n, k, d in done]}
    finally:
        lock.release()
