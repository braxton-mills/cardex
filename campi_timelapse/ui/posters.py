"""Posters for clips and daily videos (contract §7.2): the frame at 50 %, 640 px wide, JPEG, made by ffmpeg on first
request and cached as ui\\cache\\posters\\{clips|daily}\\<id>-<mtime_ns>.jpg. The source is opened only by that one
ffmpeg run (input seeking: well under a second). Posters whose source is gone or changed are deleted hourly."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from ..config import NO_WINDOW
from . import mp4
from .media import CLIP_ID_RE, DAY_RE

log = logging.getLogger("ui.posters")

KINDS = ("clips", "daily")


class Posters:
    def __init__(self, cfg, home: Path):
        self.cfg = cfg
        self.dir = home / "cache" / "posters"
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def source(self, kind: str, ident: str) -> Path | None:
        if kind == "clips" and CLIP_ID_RE.match(ident):
            p = self.cfg.paths.out / f"campi_{ident}.mp4"
        elif kind == "daily" and DAY_RE.match(ident):
            p = self.cfg.paths.daily / f"campi_daily_{ident}.mp4"
        else:
            return None
        return p if p.is_file() else None

    def get(self, kind: str, ident: str) -> Path | None:
        """Cached poster path (made now if needed), or None (unknown video, or ffmpeg failed)."""
        src = self.source(kind, ident)
        if src is None:
            return None
        try:
            stt = src.stat()
        except OSError:
            return None
        out = self.dir / kind / f"{ident}-{stt.st_mtime_ns}.jpg"
        if out.is_file():
            return out
        with self._guard:
            lock = self._locks.setdefault(f"{kind}/{ident}", threading.Lock())
        with lock:
            if out.is_file():
                return out
            out.parent.mkdir(parents=True, exist_ok=True)
            dur = mp4.duration(src, stt.st_size, stt.st_mtime_ns) or 0.0
            tmp = out.with_suffix(".tmp.jpg")
            cmd = [self.cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{dur / 2:.3f}",
                   "-i", str(src), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(tmp)]
            t0 = time.perf_counter()
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW)
                ok = r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0
            except (OSError, subprocess.SubprocessError) as e:
                r, ok = None, False
                log.warning("poster %s/%s: %s", kind, ident, e)
            if not ok:
                tmp.unlink(missing_ok=True)
                if r is not None:
                    log.warning("poster %s/%s failed: %s", kind, ident, (r.stderr or b"").decode(errors="replace")[-300:])
                return None
            tmp.replace(out)
            for old in out.parent.glob(f"{ident}-*.jpg"):  # older posters of a re-rendered video
                if old != out:
                    old.unlink(missing_ok=True)
            log.info("poster %s/%s made in %.0f ms", kind, ident, (time.perf_counter() - t0) * 1000)
            return out

    def cleanup(self) -> int:
        n = 0
        for kind in KINDS:
            d = self.dir / kind
            if not d.is_dir():
                continue
            for p in d.glob("*.jpg"):
                ident, _, mtime = p.stem.rpartition("-")
                src = self.source(kind, ident)
                try:
                    stale = src is None or str(src.stat().st_mtime_ns) != mtime
                except OSError:
                    stale = True
                if stale or p.name.endswith(".tmp.jpg"):
                    p.unlink(missing_ok=True)
                    n += 1
        if n:
            log.info("deleted %d stale posters", n)
        return n

    def cleanup_forever(self, stop: threading.Event) -> None:
        while not stop.wait(5):
            try:
                self.cleanup()
            except Exception:
                log.exception("poster cleanup failed")
            if stop.wait(3600):
                return
