"""/media/{root}/{path}: the only code that opens service files.

Windows can't replace or delete an open file, and the service relies on both (render replaces clips, housekeeping
deletes them, the archive rewrites its current part, sightings clips expire). So:
- only allow-listed names under the configured roots are served (never latest.mp4, never the current archive part,
  never .part/.tmp files, the database or models);
- no handle is held between requests or even between chunks: every read is open -> seek -> read -> close (a few ms).
  The open shares delete access, so a delete (housekeeping, sighting clip expiry) succeeds even mid-read. A replace
  onto a file can only collide with one of those few-ms reads; the service's replaces of served files (re-rendered
  clips / daily videos) go through render.replace_retry, and the archive only rewrites the current part, never served;
- Range responses are capped at RANGE_CAP bytes; the player simply asks for the next range.
"""
from __future__ import annotations

import logging
import os
import re
import time
from email.utils import formatdate
from pathlib import Path, PurePosixPath

from ..archive import CLIP_RE
from .library import ARCHIVE_RE, DAILY_RE

log = logging.getLogger("ui.media")

RANGE_CAP = 4 * 1024 * 1024
CHUNK = 1024 * 1024
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SIGHTING_FILE_RE = re.compile(r"^[A-Za-z0-9_\-]+\.(jpg|mp4)$")
TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg"}


def roots(cfg) -> dict[str, Path]:
    return {"clips": cfg.paths.out, "daily": cfg.paths.daily, "archive": cfg.paths.archive,
            "sightings": cfg.paths.sightings}


def resolve(cfg, root: str, rel: str, current_part: int) -> Path | None:
    """Absolute path for an allow-listed media file, or None."""
    base = roots(cfg).get(root)
    if base is None or not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        return None
    parts = PurePosixPath(rel).parts
    if any(p in ("", ".", "..") for p in parts):
        return None
    name = parts[-1]
    if root == "clips":
        ok = len(parts) == 1 and CLIP_RE.match(name)
    elif root == "daily":
        ok = len(parts) == 1 and DAILY_RE.match(name)
    elif root == "archive":
        m = ARCHIVE_RE.match(name) if len(parts) == 1 else None
        ok = m and int(m.group(1)) != current_part
    else:  # sightings: YYYY-MM-DD/<stem>.jpg|.mp4 written by the worker (never *.tmp.mp4, models\, recordings\)
        ok = len(parts) == 2 and DAY_RE.match(parts[0]) and SIGHTING_FILE_RE.match(name) and ".tmp" not in name
    if not ok:
        return None
    p = base.joinpath(*parts)
    try:
        real = p.resolve(strict=True)
    except OSError:
        return None
    if not real.is_relative_to(base.resolve()) or not real.is_file():
        return None
    return real


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    GENERIC_READ, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL = 0x80000000, 3, 0x80
    SHARE_ALL = 0x1 | 0x2 | 0x4  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
    INVALID = wintypes.HANDLE(-1).value

    def open_shared(path: Path):
        """Read-only file object that doesn't stop anyone else from deleting the file (a replace still waits for
        close, which is why reads are kept to a few ms)."""
        h = _k32.CreateFileW(str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if h == INVALID or h is None:
            raise FileNotFoundError(ctypes.get_last_error(), f"cannot open {path}")
        try:
            fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _k32.CloseHandle(h)
            raise
        return os.fdopen(fd, "rb", buffering=0)
else:
    def open_shared(path: Path):
        return open(path, "rb", buffering=0)


def read_at(path: Path, start: int, length: int) -> bytes:
    """open -> seek -> read -> close; nothing stays open."""
    with open_shared(path) as f:
        f.seek(start)
        out = bytearray()
        while len(out) < length:
            b = f.read(length - len(out))
            if not b:
                break
            out += b
        return bytes(out)


def parse_range(header: str, size: int) -> tuple[int, int] | None | str:
    """(start, end inclusive) for the first range of 'bytes=a-b' / 'bytes=a-' / 'bytes=-n'; 'invalid' if
    unsatisfiable; None if there is no usable Range header."""
    if not header or not header.startswith("bytes="):
        return None
    first = header[6:].split(",", 1)[0].strip()
    a, _, b = first.partition("-")
    try:
        if a == "":
            n = int(b)
            if n <= 0:
                return "invalid"
            start, end = max(0, size - n), size - 1
        else:
            start = int(a)
            end = int(b) if b else size - 1
    except ValueError:
        return None
    if start >= size or start < 0 or end < start:
        return "invalid"
    return start, min(end, size - 1)


def serve(request, path: Path, name: str):
    """Starlette response for one media request (HEAD, full or ranged GET)."""
    from starlette.responses import Response, StreamingResponse

    try:
        stt = path.stat()
    except OSError:
        return Response("gone", status_code=404)
    size = stt.st_size
    etag = f'"{size:x}-{stt.st_mtime_ns:x}"'
    ctype = TYPES.get(path.suffix.lower(), "application/octet-stream")
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "Last-Modified": formatdate(stt.st_mtime, usegmt=True),
               "Cache-Control": "public, max-age=86400" if ctype == "image/jpeg" else "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    rng = parse_range(request.headers.get("range", ""), size)
    if rng == "invalid":
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    if request.method == "HEAD":
        return Response(status_code=200, headers={**headers, "Content-Length": str(size)}, media_type=ctype)

    if rng is not None:
        start, end = rng
        end = min(end, start + RANGE_CAP - 1)
        t0 = time.perf_counter()
        try:
            data = read_at(path, start, end - start + 1)
        except OSError:
            return Response("gone", status_code=404)
        if not data:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        end = start + len(data) - 1
        log.info("206 %s bytes %d-%d/%d (%.1f MB, file open %.0f ms)", name, start, end, size, len(data) / 1e6,
                 (time.perf_counter() - t0) * 1000)
        return Response(data, status_code=206, media_type=ctype,
                        headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})

    if size <= RANGE_CAP:  # images and small files: one read
        try:
            data = read_at(path, 0, size)
        except OSError:
            return Response("gone", status_code=404)
        return Response(data, media_type=ctype, headers=headers)

    def chunks():  # no Range header on a big file: stream it, reopening per chunk; stop if it disappears
        pos = 0
        while pos < size:
            try:
                b = read_at(path, pos, min(CHUNK, size - pos))
            except OSError:
                log.info("%s went away at byte %d; response ended", name, pos)
                return
            if not b:
                return
            pos += len(b)
            yield b

    log.info("200 %s streamed in %d KB chunks (%d bytes)", name, CHUNK // 1024, size)
    return StreamingResponse(chunks(), media_type=ctype, headers={**headers, "Content-Length": str(size)})
