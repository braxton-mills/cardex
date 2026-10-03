"""MP4 duration from the moov/mvhd box (no ffprobe): walks the top-level boxes with a few small reads through a
handle that doesn't block deletes or replaces, then closes it."""
from __future__ import annotations

import struct
import threading
from pathlib import Path

_cache: dict[tuple, float | None] = {}
_lock = threading.Lock()


def _read_duration(f) -> float | None:
    pos = 0
    while True:
        f.seek(pos)
        head = f.read(16)
        if len(head) < 8:
            return None
        size, kind = struct.unpack(">I4s", head[:8])
        hdr = 8
        if size == 1:
            if len(head) < 16:
                return None
            size, hdr = struct.unpack(">Q", head[8:16])[0], 16
        elif size == 0:
            return None  # box runs to the end of the file; no moov after it
        if kind == b"moov":
            f.seek(pos + hdr)
            inner = f.read(min(size - hdr, 4096))
            i = 0
            while i + 8 <= len(inner):
                bsize, bkind = struct.unpack(">I4s", inner[i:i + 8])
                if bkind == b"mvhd":
                    body = inner[i + 8:]
                    if body[:1] == b"\x01":
                        scale, dur = struct.unpack(">IQ", body[20:32])
                    else:
                        scale, dur = struct.unpack(">II", body[12:20])
                    return round(dur / scale, 3) if scale else None
                if bsize < 8:
                    return None
                i += bsize
            return None
        if size < hdr:
            return None
        pos += size


def duration(path: Path, size: int, mtime_ns: int) -> float | None:
    """Seconds, cached per (path, size, mtime); None if unreadable."""
    from .media import open_shared
    key = (str(path), size, mtime_ns)
    with _lock:
        if key in _cache:
            return _cache[key]
    try:
        with open_shared(path) as f:
            d = _read_duration(f)
    except (OSError, struct.error):
        d = None
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = d
    return d
