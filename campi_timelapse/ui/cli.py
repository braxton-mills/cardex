"""`campi pair` and `campi devices` (contract §3.1-3.2). They only touch ui\\ui.db; the API checks ui.db on every
request, so a new code works and a revoked device stops working at once."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from ..config import NO_WINDOW
from . import auth
from .store import Store, ui_home

TAILSCALE = Path(r"C:\Program Files\Tailscale\tailscale.exe")


def tailscale_url() -> str | None:
    exe = shutil.which("tailscale") or (str(TAILSCALE) if TAILSCALE.exists() else None)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15,
                             creationflags=NO_WINDOW).stdout
        name = (json.loads(out or "{}").get("Self") or {}).get("DNSName", "").rstrip(".")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return f"https://{name}" if name else None


def base_url(cfg) -> tuple[str, str]:
    """(base URL for the pairing link, where it came from)."""
    if cfg.api.public_url:
        return cfg.api.public_url.rstrip("/"), "[api] public_url"
    ts = tailscale_url()
    if ts:
        return ts, "tailscale status"
    return f"http://127.0.0.1:{cfg.api.port}", "loopback (no Tailscale: set [api] public_url for a phone)"


def qr_text(data: str) -> str:
    """QR code for a dark console: light modules drawn, dark ones blank (two rows per line with half blocks when
    the console can show them, else '##' per module)."""
    import qrcode
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    m = [[not cell for cell in row] for row in qr.get_matrix()]  # True = light (drawn)
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "█▀▄".encode(enc)
    except (UnicodeEncodeError, LookupError):
        return "\n".join("".join("##" if c else "  " for c in row) for row in m)
    if len(m) % 2:
        m.append([False] * len(m[0]))
    chars = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}
    return "\n".join("".join(chars[(a, b)] for a, b in zip(m[i], m[i + 1])) for i in range(0, len(m), 2))


def pair(cfg) -> int:
    store = Store(ui_home(cfg) / "ui.db")
    code = auth.new_pair_code()
    store.add_pair_code(code, auth.PAIR_TTL_S)
    url, source = base_url(cfg)
    link = f"campi://pair?u={quote(url, safe='')}&c={code}"
    try:
        print(qr_text(link))
    except ImportError:
        print("(install qrcode in venv-ui for a QR code: install.ps1 -UI)")
    expires = datetime.fromtimestamp(time.time() + auth.PAIR_TTL_S).strftime("%H:%M")
    print(f"Scan with the iPhone camera, or type the URL and code into the Campi app:\n"
          f"  URL  : {url}   ({source})\n"
          f"  code : {auth.show_code(code)}   (single use, valid until {expires})\n"
          f"  link : {link}")
    if not cfg.api.enabled:
        print("note: [api] enabled = false; the phone can only pair while `campi api` is running")
    return 0


def devices(cfg, action: str | None = None, device_id: str | None = None) -> int:
    store = Store(ui_home(cfg) / "ui.db")
    if action == "revoke":
        if not device_id:
            print("usage: campi devices revoke <id>")
            return 2
        dev = store.device(device_id)
        if not dev:
            print(f"no device {device_id}")
            return 1
        if store.revoke(device_id):
            print(f"revoked {device_id} ({dev['name']}): its token and signed media links stop working now")
        else:
            print(f"{device_id} ({dev['name']}) was already revoked")
        return 0
    rows = store.devices()
    if not rows:
        print("no paired devices (campi pair)")
        return 0

    def local(iso_utc):
        return datetime.fromisoformat(iso_utc).astimezone().strftime("%Y-%m-%d %H:%M") if iso_utc else "never"

    head = f"{'id':<21} {'name':<24} {'platform':<8} {'paired':<16} {'last seen':<16} push"
    print(head)
    print("-" * len(head))
    for d in rows:
        push = f"{d['apns_env']}" if d["apns_token"] else "off"
        print(f"{d['id']:<21} {d['name'][:24]:<24} {d['platform']:<8} {local(d['created_at']):<16} "
              f"{local(d['last_seen_at']):<16} {push}")
    return 0
