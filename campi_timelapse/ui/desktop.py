"""`campi ui` launcher: the API server on a background thread of this process and a WebView2 window (pywebview) on the
main thread. Closing the window stops the server and exits the process; there are no child processes to orphan
(WebView2's msedgewebview2 helpers exit with their host). --browser serves in the foreground for a normal browser."""
from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn  # venv-ui only: an ImportError here means `install.ps1 -UI` hasn't been run

from ..config import FileLock, read_json, write_json
from .store import ui_home

log = logging.getLogger("ui")
TITLE = "Campi"


def setup_logging(home) -> None:
    """ui\\ui.log (the service's logs\\ folder is never touched); the console too when there is one (not pythonw)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(home / "ui.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").addFilter(_ignore_proactor_reset)


def _ignore_proactor_reset(record: logging.LogRecord) -> bool:
    """Windows' Proactor loop logs a traceback whenever a client resets a finished connection (python/cpython#83191);
    harmless, and players do it all the time."""
    exc = record.exc_info[1] if record.exc_info else None
    return not (isinstance(exc, ConnectionResetError) and "_call_connection_lost" in record.getMessage())


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
    if sys.stdout is not None and sys.stderr is None:
        print(msg)


def run(cfg, browser: bool = False, port: int = 8765, host: str = "127.0.0.1") -> int:
    t0 = time.monotonic()
    home = ui_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    setup_logging(home)

    lock = FileLock(home / "ui.lock")
    if not lock.acquire():
        inst = read_json(home / "instance.json", {}) or {}
        url = inst.get("url")
        if browser and url:
            webbrowser.open(url)
            say(f"campi ui is already running ({url}); opened it in the browser")
        elif focus_existing():
            say("campi ui is already running; brought its window to the front")
        else:
            say(f"campi ui is already running ({url or 'pid ' + str(inst.get('pid'))})")
        return 0

    from .app import create_app

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    app = create_app(cfg, host)
    # log_config=None: uvicorn's default console formatter calls sys.stdout.isatty(), which crashes under pythonw.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False,
                                           use_colors=False, timeout_graceful_shutdown=2, lifespan="off"))
    write_json(home / "instance.json", {"pid": os.getpid(), "url": url, "mode": "browser" if browser else "window"})
    log.info("campi ui starting (pid %d, %s, %s, sightings %s)", os.getpid(), url,
             "browser" if browser else "window", "on" if cfg.sightings.enabled else "off")
    try:
        if browser:
            def open_when_ready():
                while not server.started:
                    time.sleep(0.05)
                log.info("server ready in %.1fs; opening %s", time.monotonic() - t0, url)
                webbrowser.open(url)
            threading.Thread(target=open_when_ready, daemon=True).start()
            server.run()  # foreground; Ctrl+C stops it
            return 0
        return run_window(server, url, t0, home)
    finally:
        (home / "instance.json").unlink(missing_ok=True)
        lock.release()
        log.info("campi ui exited")


def run_window(server, url: str, t0: float, home) -> int:
    import webview

    th = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    th.start()
    while not server.started:
        if not th.is_alive():
            log.error("server did not start (port in use? try campi ui --port N)")
            return 1
        time.sleep(0.02)
    log.info("server ready in %.1fs", time.monotonic() - t0)

    window = webview.create_window(TITLE, url, width=1440, height=920, min_size=(960, 600),
                                   background_color="#0e1116", text_select=True)
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
    log.info("window closed; stopping server")
    server.should_exit = True
    th.join(timeout=5)
    if th.is_alive():
        log.warning("server thread still running after 5 s; exiting anyway")
    return 0


def main_exit(code: int) -> None:
    """Hard exit after cleanup: pythonnet/WebView2 threads must not keep the process alive."""
    logging.shutdown()
    os._exit(code)
