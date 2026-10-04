"""Long-running capture: saves one validated, de-duplicated raw JPEG every interval_s.

Frames come from the Pi's /snapshot.jpg (high-quality software JPEG) or, as a fallback, the MJPEG stream.
"""
from __future__ import annotations

import hashlib
import http.client
import logging
import os
import random
import socket
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

from . import discover
from .config import read_json, write_json
from .frames import INDEX_HEADER, day_dir

log = logging.getLogger("capture")


class StreamError(Exception):
    pass


class SnapshotMissing(StreamError):
    pass


class StreamSource:
    """Continuous MJPEG stream; every frame must be drained, the caller keeps only the due ones."""
    lazy = False

    def __init__(self, url: str, timeout: float):
        req = urllib.request.Request(url, headers={"User-Agent": "campi-timelapse"})
        self.resp = urllib.request.urlopen(req, timeout=timeout)  # applies to connect and every read
        ctype = self.resp.headers.get("Content-Type", "")
        if "boundary=" not in ctype:
            self.resp.close()
            raise StreamError(f"unexpected content type {ctype!r}")
        self.frames = iter_jpegs(self.resp, ctype.split("boundary=", 1)[1].strip().strip('"').encode())
        self.desc = f"stream {url}"

    def get(self):
        return next(self.frames)

    def close(self):
        self.resp.close()


class SnapshotSource:
    """One GET per frame, only when a frame is due."""
    lazy = True

    def __init__(self, url: str, timeout: float):
        u = urlparse(url)
        self.host, self.port, self.timeout = u.hostname, u.port or 80, timeout
        self.path = u.path + (f"?{u.query}" if u.query else "")
        # Resolve IPv4 once: trying IPv6 first makes every .local request take seconds.
        self.ip = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        self.desc = f"snapshots {url} ({self.ip})"

    def get(self):
        conn = http.client.HTTPConnection(self.ip, self.port, timeout=self.timeout)
        try:
            conn.request("GET", self.path, headers={"Host": self.host, "User-Agent": "campi-timelapse"})
            r = conn.getresponse()
            if r.status == 404:
                raise SnapshotMissing("snapshot endpoint not found (404)")
            if r.status != 200:
                raise StreamError(f"snapshot HTTP {r.status}")
            length = r.getheader("Content-Length")
            return r.read(), int(length) if length else None
        finally:
            conn.close()

    def close(self):
        pass


def iter_jpegs(resp, boundary: bytes):
    """Yield (jpeg_bytes, content_length) from a multipart/x-mixed-replace response."""
    marker = b"--" + boundary
    while True:
        line = resp.readline()
        if not line:
            raise StreamError("stream closed by server")
        if not line.strip().startswith(marker):
            continue
        length = None
        while True:
            h = resp.readline()
            if not h:
                raise StreamError("stream closed in headers")
            h = h.strip()
            if not h:
                break
            k, _, v = h.partition(b":")
            if k.strip().lower() == b"content-length":
                length = int(v.strip())
        if length is not None:
            data = resp.read(length)
        else:  # no Content-Length: read until JPEG end marker
            buf = bytearray()
            while not buf.endswith(b"\xff\xd9"):
                chunk = resp.read(1)
                if not chunk:
                    raise StreamError("stream closed mid-frame")
                buf += chunk
            data = bytes(buf)
        yield data, length


def validate(data: bytes, length: int | None, exp_w: int, exp_h: int):
    """Return mean luma (0-255) of a structurally complete, decodable JPEG, else None."""
    if length is not None and len(data) != length:
        return None
    if len(data) < 1024 or not data.startswith(b"\xff\xd8") or not data.rstrip(b"\x00\r\n").endswith(b"\xff\xd9"):
        return None
    try:
        # Full-resolution decode catches corrupt entropy data; then measure brightness on a small copy.
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    except cv2.error:
        return None
    if img is None:
        return None
    h, w = img.shape[:2]
    if exp_w and exp_h and (w, h) != (exp_w, exp_h):
        return None
    small = cv2.resize(img, (w // 8, h // 8), interpolation=cv2.INTER_AREA)
    return float(small.mean())


class FrameWriter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.day = None
        self.index = None

    def save(self, data: bytes, ts: float, luma: float, sha1: str) -> Path:
        dt = datetime.fromtimestamp(ts)
        ddir = day_dir(self.cfg, dt.date())
        if self.day != dt.date():
            if self.index:
                self.index.close()
            ddir.mkdir(parents=True, exist_ok=True)
            idx = ddir / "index.csv"
            new = not idx.exists()
            self.index = open(idx, "a", encoding="utf-8", newline="")
            if new:
                self.index.write(INDEX_HEADER + "\n")
            self.day = dt.date()
        rel = f"{dt:%H}/campi_{dt:%Y%m%d_%H%M%S}_{dt.microsecond // 1000:03d}.jpg"
        path = ddir / rel
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        self.index.write(f"{ts:.3f},{rel},{luma:.1f},{sha1},{len(data)}\n")
        self.index.flush()
        return path


def run(cfg, stop=lambda: False) -> None:
    s, c = cfg.stream, cfg.capture
    gaps = logging.getLogger("side.gaps")
    writer = FrameWriter(cfg)
    state_path = cfg.paths.state / "capture.json"
    gap_limit = c.interval_s * cfg.render.gap_factor
    backoff = s.backoff_initial_s
    # wall time of last saved frame; carried over from the previous run so restarts/reboots log their gap too
    last_saved = (read_json(state_path, {}) or {}).get("last_frame_ts")
    last_hash, dupes = None, 0
    saved = rejected = reconnects = gaps_logged = 0
    last_error = ""
    host = discover.current_host(cfg)
    fails, last_discovery = 0, float("-inf")  # consecutive connection failures; monotonic time of last MAC search

    def state(connected: bool):
        write_json(state_path, {
            "pid": os.getpid(), "updated": time.time(), "connected": connected, "host": host,
            "last_frame_ts": last_saved, "saved": saved, "rejected": rejected,
            "reconnects": reconnects, "last_error": last_error})

    mode = c.source
    log.info("capture starting: %s every %.1fs -> %s", mode, c.interval_s, cfg.paths.frames)
    while not stop():
        src = None
        try:
            src = (SnapshotSource(discover.with_host(s.snapshot_url, host), s.timeout_s) if mode == "snapshot"
                   else StreamSource(discover.with_host(s.url, host), s.timeout_s))
            log.info("connected: %s", src.desc)
            state(True)
            next_due = time.monotonic()
            while not stop():
                if src.lazy:
                    wait = next_due - time.monotonic()
                    if wait > 0:
                        time.sleep(min(wait, 0.25))
                        continue
                data, length = src.get()
                now = time.monotonic()
                if now < next_due:
                    continue  # keep draining the stream between saves
                luma = validate(data, length, c.expected_width, c.expected_height)
                if luma is None:
                    rejected += 1
                    if rejected <= 5 or rejected % 500 == 0:  # don't flood the log at 30 fps
                        log.warning("rejected corrupt/partial/wrong-size frame (%d bytes, expected %s; %d total)",
                                    len(data), length, rejected)
                    continue  # try the very next frame
                sha1 = hashlib.sha1(data).hexdigest()
                if sha1 == last_hash:
                    dupes += 1
                    rejected += 1
                    if dupes >= c.stall_after_dupes:
                        raise StreamError(f"stream stalled ({dupes} identical frames)")
                    continue
                dupes, last_hash = 0, sha1
                ts = time.time()
                if last_saved and ts - last_saved > gap_limit:
                    if not last_error and gaps_logged == 0 and saved == 0:
                        last_error = "capture not running (service stopped / PC off or asleep)"
                    gaps_logged += 1
                    gaps.info("GAP %s -> %s (%.0fs) last_error=%s",
                              datetime.fromtimestamp(last_saved).isoformat(timespec="seconds"),
                              datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
                              ts - last_saved, last_error or "-")
                    log.warning("gap of %.0fs ended", ts - last_saved)
                    last_error = ""
                writer.save(data, ts, luma, sha1)
                last_saved = ts
                saved += 1
                backoff = s.backoff_initial_s
                fails = 0
                next_due += c.interval_s
                if next_due < now:  # fell behind (e.g. after a slow disk write): don't burst
                    next_due = now + c.interval_s
                state(True)
        except SnapshotMissing:
            log.error("%s returned 404 (stock server script?); falling back to the MJPEG stream", s.snapshot_url)
            mode = "stream"
        except Exception as e:  # network errors, timeouts, stalls, server restarts
            last_error = f"{type(e).__name__}: {e}"
            reconnects += 1
            fails += 1
            # Two failures in a row: the Pi may have a new IP (DHCP) or its name stopped resolving.
            if fails >= 2 and time.monotonic() - last_discovery >= 60:
                last_discovery = time.monotonic()
                try:
                    ip = discover.find_pi(cfg)
                except Exception as de:
                    ip = None
                    log.warning("Pi search by MAC failed: %s", de)
                if ip and ip != host:
                    log.warning("Pi found at %s by MAC (was %s); switching", ip, host)
                    host, backoff = ip, s.backoff_initial_s
            state(False)
            delay = min(backoff, s.backoff_max_s) * random.uniform(0.8, 1.2)
            log.warning("stream error: %s; reconnecting in %.1fs", last_error, delay)
            end = time.monotonic() + delay
            while time.monotonic() < end and not stop():
                time.sleep(0.2)
            backoff = min(backoff * 2, s.backoff_max_s)
        finally:
            if src:
                src.close()
    log.info("capture stopped")
