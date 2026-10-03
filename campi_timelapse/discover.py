"""Find the Pi by its MAC address when its name (campi.local) or last known IP stops working.

The LAN is DHCP and mDNS is unreliable, so capture calls find_pi() after repeated connection failures:
probe every host on each local /24 (fills the ARP table), then look the MAC up with `arp -a`.
The IP found is remembered in state/pi_host.json and used instead of the URL's host from then on.
"""
from __future__ import annotations

import logging
import re
import select
import socket
import subprocess
import time
from urllib.parse import urlparse, urlunparse

from .config import NO_WINDOW, read_json, write_json

log = logging.getLogger("discover")

ARP_LINE = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f]{2}(?:-[0-9a-f]{2}){5})\b", re.I)


def norm_mac(mac: str) -> str:
    return mac.strip().lower().replace(":", "-")


def current_host(cfg) -> str:
    """Last IP found by MAC, else the host in the configured URL."""
    cached = read_json(cfg.paths.state / "pi_host.json", {}) or {}
    mac = norm_mac(getattr(cfg.stream, "pi_mac", "") or "")
    if mac and cached.get("ip") and cached.get("mac") == mac:
        return cached["ip"]
    return urlparse(cfg.stream.snapshot_url).hostname


def with_host(url: str, host: str) -> str:
    u = urlparse(url)
    return urlunparse(u._replace(netloc=f"{host}:{u.port}" if u.port else host))


def _local_prefixes() -> list[str]:
    ips = {ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    return sorted({ip.rsplit(".", 1)[0] for ip in ips if not ip.startswith(("127.", "169.254."))})


def _sweep(prefix: str, port: int, wait: float = 1.5) -> set[str]:
    """Non-blocking connect to port on prefix.1-254; returns the IPs that accepted."""
    socks = {}
    for i in range(1, 255):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setblocking(False)
        s.connect_ex((f"{prefix}.{i}", port))
        socks[s] = f"{prefix}.{i}"
    accepted, pending = set(), list(socks)
    end = time.monotonic() + wait
    try:
        while pending and (left := end - time.monotonic()) > 0:
            _, w, x = select.select([], pending, pending, left)
            for s in w:
                if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    accepted.add(socks[s])
            done = set(w) | set(x)
            pending = [s for s in pending if s not in done]
    finally:
        for s in socks:
            s.close()
    return accepted


def _arp() -> dict[str, list[str]]:
    out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10,
                         creationflags=NO_WINDOW).stdout
    macs: dict[str, list[str]] = {}
    for ip, mac in ARP_LINE.findall(out):
        macs.setdefault(mac.lower(), []).append(ip)
    return macs


def find_pi(cfg) -> str | None:
    """IP currently answering for stream.pi_mac (preferring one with the stream port open), or None."""
    mac = norm_mac(getattr(cfg.stream, "pi_mac", "") or "")
    if not mac:
        return None
    port = urlparse(cfg.stream.snapshot_url).port or 80
    accepted = set()
    for prefix in _local_prefixes():
        accepted |= _sweep(prefix, port)
    ips = _arp().get(mac, [])
    found = next((ip for ip in ips if ip in accepted), ips[0] if ips else None)
    if found:
        write_json(cfg.paths.state / "pi_host.json", {"ip": found, "mac": mac, "found": time.time()})
    log.info("MAC %s -> %s (port %d open on: %s)", mac, found or "not found", port,
             ", ".join(sorted(accepted)) or "none")
    return found
