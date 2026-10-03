"""`campi ui` launcher: a WebView2 window (pywebview) onto the Campi API.

The desktop is a paired device like any phone (device `desktop`). Each launch gives it a fresh token (only its hash
is stored), handed to the page in the URL fragment, which never reaches the server or the logs.

- If the API runs under the service ([api] enabled = true, or `campi api`), the window opens onto it.
- Otherwise this process serves the API itself on a free loopback port, on a background thread, without push
  notifications; closing the window stops it. --browser serves in the foreground for a normal browser.

"Show in folder" goes through the window (js_api), so Explorer opens in this desktop session even when the API runs
in session 0 under the service.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from ..config import NO_WINDOW, FileLock, read_json, write_json
from . import auth
from .server import make_server, setup_logging
from .store import Store, ui_home

log = logging.getLogger("ui")
TITLE = "Campi"


def focus_existing() -> bool:
    """Bring an already-open Campi window to the front."""
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def set_window_icon() -> None:
    """Taskbar / title-bar icon (WinForms would otherwise show pythonw's)."""
    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
    hwnd = user32.FindWindowW(None, TITLE)
    ico = str(Path(__file__).parent / "static" / "campi.ico")
    for which, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
        icon = user32.LoadImageW(None, ico, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if hwnd and icon:
            user32.SendMessageW(hwnd, 0x80, which, icon)  # WM_SETICON


def say(msg: str) -> None:
    log.info(msg)


def desktop_token(store: Store) -> str:
    """A fresh token for the `desktop` device (created on first use); the previous one stops working."""
    did = store.get("desktop_device_id")
    dev = store.device(did) if did else None
    if dev is None or dev["revoked_at"]:
        token, dev = auth.create_device(store, "desktop", "desktop")
        store.put("desktop_device_id", dev["id"])  # desktop-only endpoints answer this device alone
        log.info("created the desktop device %s", dev["id"])
        return token
    token = auth.new_token()
    store.set_token(dev["id"], auth.token_hash(token))
    return token


def running_api(port: int) -> str | None:
    """Base URL of a Campi API already listening on 127.0.0.1:port (it answers 401 without a token)."""
    url = f"http://127.0.0.1:{port}"
    try:
        urllib.request.urlopen(f"{url}/api/status", timeout=1.5)
    except urllib.error.HTTPError as e:
        try:
            if e.code == 401 and json.loads(e.read()).get("error", {}).get("code") == "unauthorized":
                return url
        except ValueError:
            pass
    except (OSError, ValueError):
        pass
    return None


class Shell:
    """pywebview js_api: things the page asks the window process to do in this desktop session."""

    def __init__(self, cfg):
        self._cfg = cfg

    def reveal(self, root: str, path: str) -> dict:
        from .media import resolve
        p = resolve(self._cfg, str(root), str(path), current_part=-1)  # the current archive part may be shown
        if p is None:
            return {"error": "That file no longer exists."}
        subprocess.Popen(["explorer.exe", f"/select,{p}"], creationflags=NO_WINDOW)
        return {"shown": str(p)}


def run(cfg, browser: bool = False, port: int | None = None, host: str = "127.0.0.1") -> int:
    t0 = time.monotonic()
    home = ui_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    setup_logging(home, "ui")

    lock = FileLock(home / "ui.lock")
    if not lock.acquire():
        inst = read_json(home / "instance.json", {}) or {}
        if focus_existing():
            say("campi ui is already running; brought its window to the front")
        else:
            say(f"campi ui is already running ({inst.get('url') or 'pid ' + str(inst.get('pid'))})")
        return 0

    try:
        store = Store(home / "ui.db")
        token = desktop_token(store)
        base = running_api(int(cfg.api.port)) if port is None else None
        server = th = None
        if base:
            log.info("attaching to the API on %s", base)
        else:
            from .app import create_app
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((host, port or 0))
            port = sock.getsockname()[1]
            base = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
            server = make_server(create_app(cfg), host, port)
            th = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="uvicorn", daemon=True)
        url = f"{base}/"
        write_json(home / "instance.json", {"pid": os.getpid(), "url": url, "mode": "browser" if browser else "window",
                                            "server": "own" if server else "attached"})
        log.info("campi ui starting (pid %d, %s, %s, %s server, sightings %s)", os.getpid(), url,
                 "browser" if browser else "window", "own" if server else "the service's",
                 "on" if cfg.sightings.enabled else "off")
        if th:
            th.start()
            while not server.started:
                if not th.is_alive():
                    log.error("server did not start (port in use? try campi ui --port N)")
                    return 1
                time.sleep(0.02)
            log.info("server ready in %.1fs", time.monotonic() - t0)
        page = f"{url}#token={token}"
        if browser:
            webbrowser.open(page)
            if th:
                say(f"serving {url} for the browser; Ctrl+C stops")
                try:
                    while th.is_alive():
                        th.join(0.5)
                except KeyboardInterrupt:
                    server.should_exit = True
                    th.join(5)
            return 0
        rc = run_window(cfg, page, t0, home)
        if server:
            log.info("window closed; stopping server")
            server.should_exit = True
            th.join(timeout=5)
            if th.is_alive():
                log.warning("server thread still running after 5 s; exiting anyway")
        return rc
    finally:
        (home / "instance.json").unlink(missing_ok=True)
        lock.release()
        log.info("campi ui exited")


def run_window(cfg, page: str, t0: float, home) -> int:
    import webview

    window = webview.create_window(TITLE, page, width=1440, height=920, min_size=(960, 600),
                                   background_color="#0e1116", text_select=True, js_api=Shell(cfg))
    shown = {"done": False}

    def loaded():
        if not shown["done"]:
            shown["done"] = True
            log.info("window loaded in %.1fs", time.monotonic() - t0)
            try:
                set_window_icon()
            except Exception:
                log.exception("could not set the window icon")

    def set_hidden(hidden: bool):
        # the Live view drops its stream while minimized (the page can't see a minimize by itself)
        try:
            window.evaluate_js(f"window.campi && window.campi.setHidden({'true' if hidden else 'false'})")
        except Exception:
            log.exception("could not tell the page about %s", "minimize" if hidden else "restore")

    window.events.loaded += loaded
    window.events.minimized += lambda: set_hidden(True)
    window.events.restored += lambda: set_hidden(False)
    # WebView2 profile (cache, remembered filters) under ui\webview, not in a temp folder
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(home / "webview"))
    return 0


def main_exit(code: int) -> None:
    """Hard exit after cleanup: pythonnet/WebView2 threads must not keep the process alive."""
    logging.shutdown()
    os._exit(code)
