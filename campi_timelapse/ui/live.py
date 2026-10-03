"""/live.mjpg: the Pi's MJPEG stream, proxied. One upstream connection per viewer, closed as soon as the viewer goes
away (every viewer is another full ~23 Mbit/s stream over the Pi's Wi-Fi). The Pi host comes from the same lookup as
capture (state\\pi_host.json, else the configured host); the UI never sweeps the LAN itself."""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from urllib.parse import urlparse

from ..discover import current_host, with_host

log = logging.getLogger("ui.live")

MAX_VIEWERS = 2
READ = 64 * 1024
DISCONNECT_CHECK_S = 0.5

_active = 0
_ids = itertools.count(1)


async def _connect(cfg):
    """(reader, writer, content_type) of an upstream stream connection, or raises with a reason."""
    url = with_host(cfg.stream.url, current_host(cfg))
    u = urlparse(url)
    timeout = float(cfg.stream.timeout_s)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(u.hostname, u.port or 80), timeout)
    try:
        path = u.path + (f"?{u.query}" if u.query else "")
        writer.write(f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nConnection: close\r\n"
                     "User-Agent: campi-ui\r\n\r\n".encode())
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
        lines = head.decode("latin-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "200":
            raise ConnectionError(f"{url}: {lines[0]}")
        hdrs = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
        if "chunked" in hdrs.get("transfer-encoding", ""):
            raise ConnectionError(f"{url}: chunked responses are not supported")
        return reader, writer, hdrs.get("content-type", "multipart/x-mixed-replace")
    except BaseException:
        writer.close()
        raise


async def live(request, cfg):
    from starlette.responses import Response, StreamingResponse

    global _active
    if _active >= MAX_VIEWERS:
        return Response(f"already {_active} live viewers", status_code=503)
    try:
        reader, writer, ctype = await _connect(cfg)
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError) as e:
        log.warning("live: upstream unavailable: %s", e or type(e).__name__)
        return Response(f"camera stream unavailable: {e or type(e).__name__}", status_code=502)
    vid = next(_ids)
    _active += 1
    log.info("live: viewer %d opened (%s; %d active)", vid, writer.get_extra_info("peername"), _active)

    async def body():
        global _active
        t0 = last_check = time.monotonic()
        sent = 0
        why = "upstream ended"
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(READ), float(cfg.stream.timeout_s))
                if not chunk:
                    break
                sent += len(chunk)
                yield chunk
                if time.monotonic() - last_check > DISCONNECT_CHECK_S:
                    last_check = time.monotonic()
                    if await request.is_disconnected():
                        why = "viewer left"
                        break
        except asyncio.TimeoutError:
            why = "upstream stalled"
        except (asyncio.CancelledError, GeneratorExit):
            why = "viewer left"
            raise
        finally:
            writer.close()
            _active -= 1
            log.info("live: viewer %d closed after %.1fs, %.1f MB (%s; %d active)", vid, time.monotonic() - t0,
                     sent / 1e6, why, _active)

    return StreamingResponse(body(), media_type=ctype, headers={"Cache-Control": "no-store"})
