"""`campi_timelapse api`: the API on 127.0.0.1:[api] port, headless. Run by the supervisor (pythonw, below-normal
priority, in its Job Object) when [api] enabled = true, or in a console with `campi api` for debugging.

Only this process sends push notifications ([api.push]); a server that `campi ui` starts for itself never does.
The push sender and the hourly poster cleanup start only once the port is bound, so a second instance (which fails to
bind) can never send duplicates. All its files are in CampiTimelapse\\ui\\ (api.log, api.lock, ui.db, cache\\).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import time

from ..config import FileLock
from .store import ui_home

log = logging.getLogger("ui.api")


def setup_logging(home, name: str) -> None:
    """ui\\<name>.log (the service's logs\\ folder is never touched); the console too when there is one."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(home / f"{name}.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").addFilter(ignore_proactor_reset)
    logging.getLogger("uvicorn.error").addFilter(ignore_cut_short)


def ignore_cut_short(record: logging.LogRecord) -> bool:
    """A media response that ended early because its file was deleted is expected (app.is_cut_short logs it)."""
    from .app import is_cut_short
    return not (record.exc_info and is_cut_short(record.exc_info[1]))


def ignore_proactor_reset(record: logging.LogRecord) -> bool:
    """Windows' Proactor loop logs a traceback whenever a client resets a finished connection (python/cpython#83191);
    harmless, and players do it all the time."""
    exc = record.exc_info[1] if record.exc_info else None
    return not (isinstance(exc, ConnectionResetError) and "_call_connection_lost" in record.getMessage())


def make_server(app, host: str, port: int):
    import uvicorn
    # log_config=None: uvicorn's default console formatter calls sys.stdout.isatty(), which crashes under pythonw.
    return uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False,
                                         use_colors=False, timeout_graceful_shutdown=2, lifespan="off"))


def run_api(cfg, port: int | None = None) -> int:
    home = ui_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    setup_logging(home, "api")
    port = int(port or cfg.api.port)
    lock = FileLock(home / "api.lock")
    if not lock.acquire():
        log.error("another campi api is already running; exiting")
        return 1
    try:
        from .app import create_app
        app = create_app(cfg)
        server = make_server(app, "127.0.0.1", port)
        stop = threading.Event()

        def background():
            while not server.started:
                if stop.wait(0.1):
                    return
            log.info("api ready on http://127.0.0.1:%d (pid %d, sightings %s, push %s)", port, os.getpid(),
                     "on" if cfg.sightings.enabled else "off", push_state(cfg))
            threading.Thread(target=app.state.posters.cleanup_forever, args=(stop,), daemon=True,
                             name="poster-cleanup").start()
            if push_state(cfg) == "on":
                from .push import PushSender
                PushSender(cfg, app.state.store, app.state.sightings).start(stop)

        threading.Thread(target=background, daemon=True, name="api-start").start()
        t0 = time.monotonic()
        server.run()
        stop.set()
        if not server.started:
            log.error("could not listen on 127.0.0.1:%d (in use? another campi api or campi ui)", port)
            return 1
        log.info("api stopped after %.0fs", time.monotonic() - t0)
        return 0
    finally:
        lock.release()


def push_state(cfg) -> str:
    p = cfg.api.push
    if not p.enabled:
        return "off"
    if not os.path.isfile(p.key_file):
        return "off (key file missing)"
    if not (p.key_id and p.team_id and p.bundle_id):
        return "off (key_id / team_id / bundle_id not set)"
    return "on"
