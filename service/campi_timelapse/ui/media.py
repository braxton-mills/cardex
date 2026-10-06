"""/media/{root}/{path}: the only code that opens service files (contract §7.1).

Windows can't replace or delete an open file unless it was opened with delete sharing, and the service relies on
both (render replaces clips, housekeeping deletes them, the archive rewrites its current part, sightings clips
expire). So:
- only allow-listed names under the configured roots are served (never latest.mp4, never the current archive part,
  never .part/.tmp files, the database or models);
- every read opens with FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, and no handle is held between
  requests or even between chunks: open -> seek -> read -> close (a few ms). A delete succeeds even mid-read; a
  replace can only collide with one of those few-ms reads (render.replace_retry retries);
- a response is exactly the range requested (or the whole file), streamed in CHUNK reads when it's large. A file that
  disappears mid-response ends the response early, which closes the connection.
"""
from __future__ import annotations

import logging
import os
import re
from email.utils import formatdate
from pathlib import Path, PurePosixPath

from ..archive import CLIP_RE
from ..custom_jobs import CUSTOM_RE
from .contract import ApiError

log = logging.getLogger("ui.media")

ONE_READ_MAX = 4 * 1024 * 1024  # larger responses are streamed in CHUNK reads
CHUNK = 1024 * 1024
DAILY_RE = re.compile(r"^campi_daily_(\d{4}-\d{2}-\d{2})\.mp4$")
ARCHIVE_RE = re.compile(r"^campi_archive_(\d{3})\.mp4$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLIP_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}$")
SIGHTING_FILE_RE = re.compile(r"^[A-Za-z0-9_\-]+\.(jpg|mp4)$")
MESH_FILE_RE = re.compile(r"^[a-z0-9\-]+\.glb$")
TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".glb": "model/gltf-binary"}


def roots(cfg) -> dict[str, Path]:
    return {"clips": cfg.paths.out, "daily": cfg.paths.daily, "archive": cfg.paths.archive,
            "sightings": cfg.paths.sightings, "cards": cfg.paths.card_meshes, "custom": cfg.paths.custom}


def resolve(cfg, root: str, rel: str, current_part: int) -> Path | None:
    """Absolute path for an allow-listed media file (posters excluded), or None."""
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
    elif root == "custom":  # desktop UI custom timelapses (never .part files)
        ok = len(parts) == 1 and CUSTOM_RE.match(name)
    elif root == "cards":  # <slug>-<sid8>.glb written by card_meshes (never manifest.json or *.tmp)
        ok = len(parts) == 1 and MESH_FILE_RE.match(name)
    else:  # sightings: YYYY-MM-DD/<stem>.jpg|.mp4 written by the worker (never *.tmp.mp4, models\, recordings\)
        ok = len(parts) == 2 and DAY_RE.match(parts[0]) and SIGHTING_FILE_RE.match(name) and ".tmp" not in name
    if not ok:
        return None
    p = base.joinpath(*parts)
    try:
        real = p.resolve(strict=True)
        if not real.is_relative_to(base.resolve()) or not real.is_file():
            return None
    except OSError:
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
    """(start, end inclusive) for a single 'bytes=a-b' / 'bytes=a-' / 'bytes=-n'; 'invalid' if unsatisfiable;
    None if there is no usable single range (multi-range requests get the full body)."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    a, _, b = header[6:].strip().partition("-")
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


def etag_of(stt) -> str:
    return f'"{stt.st_size}-{stt.st_mtime_ns}"'


def serve(request, path: Path, name: str, cache_control: str):
    """Starlette response for one media request (HEAD, full or ranged GET; 304; If-Range)."""
    from starlette.responses import Response, StreamingResponse

    try:
        stt = path.stat()
    except OSError:
        raise ApiError(404, "not_found", f"{name} no longer exists") from None
    size = stt.st_size
    etag = etag_of(stt)
    ctype = TYPES.get(path.suffix.lower(), "application/octet-stream")
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "Last-Modified": formatdate(stt.st_mtime, usegmt=True),
               "Cache-Control": cache_control}
    inm = request.headers.get("if-none-match")
    if inm and etag in [t.strip() for t in inm.split(",")]:
        return Response(status_code=304, headers=headers)
    rng_header = request.headers.get("range", "")
    if_range = request.headers.get("if-range")
    rng = parse_range(rng_header, size) if (not if_range or if_range == etag) else None
    if rng == "invalid":
        e = ApiError(416, "invalid_range", f"unsatisfiable range {rng_header!r} for {size} bytes")
        import json
        body = json.dumps(e.body()).encode()
        return Response(body, status_code=416, media_type="application/json",
                        headers={**headers, "Content-Range": f"bytes */{size}"})

    # Exactly the bytes asked for (AVPlayer doesn't accept a 206 shorter than the range it requested)
    if rng is not None:
        start, end = rng
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        start, end, status = 0, size - 1, 200
    length = end - start + 1
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=ctype)

    if length <= ONE_READ_MAX:  # images, and the small ranges players probe with: one read
        try:
            data = read_at(path, start, length)
        except OSError:
            raise ApiError(404, "not_found", f"{name} no longer exists") from None
        if len(data) != length:  # shrank or was replaced between stat and read: let the client retry
            raise ApiError(404, "not_found", f"{name} changed while reading")
        return Response(data, status_code=status, media_type=ctype, headers=headers)

    def chunks():  # reopen per chunk (nothing stays open between them); stop if the file disappears
        pos = start
        while pos <= end:
            try:
                b = read_at(path, pos, min(CHUNK, end + 1 - pos))
            except OSError:
                log.info("%s went away at byte %d; response ended", name, pos)
                return
            if not b:
                log.info("%s shrank at byte %d; response ended", name, pos)
                return
            pos += len(b)
            yield b

    log.info("%d %s bytes %d-%d/%d streamed in %d KB chunks", status, name, start, end, size, CHUNK // 1024)
    return StreamingResponse(chunks(), status_code=status, media_type=ctype, headers=headers)
