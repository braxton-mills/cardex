"""Live view (contract §8).

/live.mjpg: the Pi's MJPEG stream, proxied frame by frame as multipart/x-mixed-replace; boundary=campiframe (each
part with Content-Type and Content-Length, JPEGs passed through unchanged), optionally thinned to max_fps per viewer.
One upstream connection per viewer, closed as soon as the viewer goes away (every viewer is another full stream
over the Pi's Wi-Fi). The Pi host comes from the same lookup as capture (state\\pi_host.json, else the configured
host); the API never sweeps the LAN itself.

/live.jpg: the newest frame capture already saved (no request to the Pi), rotated by [image] rotation, scaled to
?w=, JPEG q80.
"""
from __future__ import annotations

import asyncio
import io
import itertools
import logging
import socket
import threading
import time
from urllib.parse import urlparse

from .. import frames as fr
from ..discover import current_host, with_host
from .auth import RateLimit
from .contract import ApiError, int_param, iso, local_day
from .media import open_shared, read_at

log = logging.getLogger("ui.live")

READ = 64 * 1024
DISCONNECT_CHECK_S = 0.5
MAX_HEADER = 16 * 1024
MAX_FRAME = 16 * 1024 * 1024
BOUNDARY = b"campiframe"


# ---------------------------------------------------------------- reachability (Status.live.available)

class LiveProbe:
    """Can the API reach the Pi's stream port? Checked in the background at most every 30 s."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.available = False
        self.checked = 0.0
        self._lock = threading.Lock()
        self._running = False

    def get(self) -> bool:
        with self._lock:
            due = not self._running and time.monotonic() - self.checked > 30
            if due:
                self._running = True
        if due:
            threading.Thread(target=self._check, daemon=True, name="live-probe").start()
        return self.available

    def check_now(self) -> bool:
        with self._lock:
            self._running = True
        self._check()
        return self.available

    def _check(self):
        try:
            u = urlparse(self.cfg.stream.url)
            host = current_host(self.cfg)
            ok = False
            if host:
                with socket.create_connection((host, u.port or 80), timeout=2):
                    ok = True
        except OSError:
            ok = False
        finally:
            with self._lock:
                self._running = False
                self.checked = time.monotonic()
        self.available = ok


# ---------------------------------------------------------------- /live.mjpg

class LiveStreams:
    def __init__(self, cfg):
        self.cfg = cfg
        self.active = 0
        self.ids = itertools.count(1)

    async def _connect(self):
        """(reader, writer, upstream boundary) of a new stream connection; raises with a reason."""
        url = with_host(self.cfg.stream.url, current_host(self.cfg))
        u = urlparse(url)
        timeout = float(self.cfg.stream.timeout_s)
        reader, writer = await asyncio.wait_for(asyncio.open_connection(u.hostname, u.port or 80), timeout)
        try:
            path = u.path + (f"?{u.query}" if u.query else "")
            writer.write(f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nConnection: close\r\n"
                         "User-Agent: campi-api\r\n\r\n".encode())
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
            lines = head.decode("latin-1").split("\r\n")
            status = lines[0].split(" ", 2)
            if len(status) < 2 or status[1] != "200":
                raise ConnectionError(f"{url}: {lines[0]}")
            hdrs = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
            if "chunked" in hdrs.get("transfer-encoding", ""):
                raise ConnectionError(f"{url}: chunked responses are not supported")
            ctype = hdrs.get("content-type", "")
            if "boundary=" not in ctype:
                raise ConnectionError(f"{url}: not a multipart stream ({ctype})")
            boundary = ctype.split("boundary=", 1)[1].split(";")[0].strip().strip('"')
            if boundary.startswith("--"):
                boundary = boundary[2:]
            return reader, writer, boundary.encode()
        except BaseException:
            writer.close()
            raise

    async def frames(self, reader, boundary: bytes):
        """JPEG payloads of the upstream multipart stream (Content-Length when given, else up to the next boundary)."""
        timeout = float(self.cfg.stream.timeout_s)
        marker = b"--" + boundary
        buf = b""

        async def more():
            nonlocal buf
            chunk = await asyncio.wait_for(reader.read(READ), timeout)
            if not chunk:
                raise EOFError
            buf += chunk

        try:
            while True:
                while (i := buf.find(marker)) < 0:
                    buf = buf[-len(marker):]
                    await more()
                buf = buf[i + len(marker):]
                while (j := buf.find(b"\r\n\r\n")) < 0:
                    if len(buf) > MAX_HEADER:
                        raise ConnectionError("part headers too long")
                    await more()
                head, buf = buf[:j], buf[j + 4:]
                if head.startswith(b"--"):
                    return  # closing boundary
                n = None
                for line in head.split(b"\r\n"):
                    k, _, v = line.partition(b":")
                    if k.strip().lower() == b"content-length":
                        n = int(v.strip())
                if n is not None:
                    if n > MAX_FRAME:
                        raise ConnectionError("frame too large")
                    while len(buf) < n:
                        await more()
                    data, buf = buf[:n], buf[n:]
                else:
                    while (k := buf.find(marker)) < 0:
                        if len(buf) > MAX_FRAME:
                            raise ConnectionError("frame too large")
                        await more()
                    data, buf = buf[:k].rstrip(b"\r\n"), buf[k:]
                yield data
        except EOFError:
            return

    async def response(self, request, q):
        from starlette.responses import StreamingResponse

        max_fps = int_param(q, "max_fps", 30, 1, 30)
        limit = int(self.cfg.api.max_live_viewers)
        if self.active >= limit:
            raise ApiError(503, "live_busy", f"{limit} viewers are already watching")
        self.active += 1  # reserved now: a slow upstream connect must not let more viewers in
        vid = next(self.ids)
        try:
            reader, writer, boundary = await self._connect()
            gen = self.frames(reader, boundary)
            first = await asyncio.wait_for(gen.__anext__(), float(self.cfg.stream.timeout_s))
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError,
                StopAsyncIteration) as e:
            self.active -= 1
            if "writer" in locals():
                writer.close()
            log.warning("live: Pi unreachable: %s", e or type(e).__name__)
            raise ApiError(502, "pi_unreachable", f"the camera stream didn't answer ({e or type(e).__name__})") from None
        except BaseException:
            self.active -= 1
            raise
        log.info("live: viewer %d opened (max_fps %d; %d active)", vid, max_fps, self.active)

        def part(data: bytes) -> bytes:
            return (b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode()
                    + b"\r\n\r\n" + data + b"\r\n")

        async def body():
            t0 = last_check = time.monotonic()
            last_sent = 0.0
            period = 1.0 / max_fps
            sent = frames_out = 0
            why = "upstream ended"
            try:
                yield part(first)
                last_sent = time.monotonic()
                async for data in gen:
                    now = time.monotonic()
                    if now - last_sent >= period * 0.95:
                        chunk = part(data)
                        sent += len(chunk)
                        frames_out += 1
                        last_sent = now
                        yield chunk
                    if now - last_check > DISCONNECT_CHECK_S:
                        last_check = now
                        if await request.is_disconnected():
                            why = "viewer left"
                            break
            except asyncio.TimeoutError:
                why = "upstream stalled"
            except ConnectionError as e:
                why = f"upstream error: {e}"
            except (asyncio.CancelledError, GeneratorExit):
                why = "viewer left"
                raise
            finally:
                writer.close()
                await gen.aclose()
                self.active -= 1
                log.info("live: viewer %d closed after %.1fs, %d frames, %.1f MB (%s; %d active)", vid,
                         time.monotonic() - t0, frames_out + 1, sent / 1e6, why, self.active)

        return StreamingResponse(body(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}",
                                 headers={"Cache-Control": "no-store", "Connection": "close"})


# ---------------------------------------------------------------- /live.jpg

class Snapshot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.limit = RateLimit(4, 1.0)
        self._cache: tuple | None = None   # (key, bytes, frame ts)
        self._lock = threading.Lock()

    def newest_frame(self) -> tuple[float, object] | None:
        """(ts, path) of the last row of today's index.csv (else yesterday's), read from the end of the file."""
        now = time.time()
        for d in (local_day(now), local_day(now - 86400)):
            idx = fr.day_dir(self.cfg, d) / "index.csv"
            try:
                with open_shared(idx) as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 8192))
                    tail = f.read()
            except OSError:
                continue
            for line in reversed(tail.decode("utf-8", "replace").splitlines()):
                cells = line.split(",")
                if len(cells) < 3:
                    continue
                try:
                    return float(cells[0]), fr.day_dir(self.cfg, d) / cells[1]
                except ValueError:
                    continue  # header or a partly written line
        return None

    def get(self, q) -> tuple[bytes, float]:
        if not self.limit.hit():
            raise ApiError(429, "rate_limited", "at most 4 /live.jpg requests per second")
        w = int_param(q, "w", 1080, 16, 8192)
        found = self.newest_frame()
        if not found or found[0] < time.time() - 600:
            raise ApiError(404, "not_found", "no frame saved in the last 10 minutes")
        ts, path = found
        key = (str(path), w)
        with self._lock:
            if self._cache and self._cache[0] == key:
                return self._cache[1], ts
        try:
            raw = read_at(path, 0, path.stat().st_size)
        except OSError:
            raise ApiError(404, "not_found", "the newest frame is gone") from None
        jpg = self.render(raw, w)
        with self._lock:
            self._cache = (key, jpg, ts)
        return jpg, ts

    def render(self, raw: bytes, w: int) -> bytes:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        rot = int(self.cfg.image.rotation)
        native_w = img.height if rot in (90, 270) else img.width
        w = min(w, native_w)
        scale = w / native_w
        if scale < 1:
            img.draft("RGB", (max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        img = img.convert("RGB")
        if rot:  # [image] rotation is clockwise
            img = img.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                                 270: Image.Transpose.ROTATE_90}[rot])
        if img.width != w:
            img = img.resize((w, max(1, round(img.height * w / img.width))), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80)
        return out.getvalue()


def frame_time_header(ts: float) -> str:
    return iso(ts)
