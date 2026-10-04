"""The Campi HTTP API (api/api-contract.md, v1) plus the desktop UI's static frontend.

Every /api request needs `Authorization: Bearer <token>` (also from 127.0.0.1: `tailscale serve` traffic arrives
from loopback), except POST /api/pair. /media and /live accept a bearer token or a signed URL. Endpoints outside the
contract (/api/today, /api/activity, /api/cards, /api/clips/newest, /api/desktop/*) answer only the `desktop` device.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
from typing import Any

from fastapi import Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import NO_WINDOW
from . import media, status_api
from .auth import Auth
from .contract import ApiError, flag, int_param, invalid, iso, iso_from_utc, not_found, one, parse_instant
from .highlights import Highlights
from .library import Library
from .live import LiveProbe, LiveStreams, Snapshot
from .posters import Posters
from .sightings_data import Sightings
from .store import Store, ui_home

log = logging.getLogger("ui")
STATIC = os.path.join(os.path.dirname(__file__), "static")
PUSH_KEYS = ("new_catch", "rare", "discovered", "service_alerts")
APNS_TOKEN_RE = re.compile(r"^[0-9A-Fa-f]{8,400}$")


class NoStoreJSON(JSONResponse):
    def __init__(self, content: Any = None, status_code: int = 200, headers=None, **kw):
        super().__init__(content, status_code, {"Cache-Control": "no-store", **(headers or {})}, **kw)

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class RevalidatedStatic(StaticFiles):
    """Always revalidate the frontend (cheap 304s): after an update, a cached old lib.js next to a new view module
    would break the module imports."""

    def file_response(self, *a, **kw):
        resp = super().file_response(*a, **kw)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def device_json(d: dict) -> dict:
    return {"id": d["id"], "name": d["name"], "platform": d["platform"], "created_at": iso_from_utc(d["created_at"]),
            "last_seen_at": iso_from_utc(d["last_seen_at"]),
            "push": {"enabled": d["apns_token"] is not None,
                     "environment": d["apns_env"] if d["apns_env"] in ("sandbox", "production") else None,
                     "prefs": {k: bool(d["prefs"].get(k)) for k in PUSH_KEYS}}}


def is_cut_short(e: BaseException | None) -> bool:
    """h11's complaint when a streamed media response ends before its Content-Length (the file was deleted)."""
    return type(e).__name__ == "LocalProtocolError" and "Too little data" in str(e)


def in_console_session() -> bool:
    """True when this process runs in the interactive desktop session (not session 0 under the service task)."""
    if os.name != "nt":
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    sid = ctypes.c_ulong()
    if not k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid)):
        return False
    return sid.value != 0 and sid.value == k32.WTSGetActiveConsoleSessionId()


def create_app(cfg) -> FastAPI:
    home = ui_home(cfg)
    store = Store(home / "ui.db")
    auth = Auth(cfg, store)
    sd = Sightings(cfg, store)
    lib = Library(cfg, store, sd)
    hl = Highlights(cfg, store, sd, lib)
    posters = Posters(cfg, home)
    streams = LiveStreams(cfg)
    snapshot = Snapshot(cfg)
    probe = LiveProbe(cfg)
    probe.get()  # first reachability check in the background, so the first /api/status already knows
    app = FastAPI(title="Campi", version="1", docs_url=None, redoc_url=None, openapi_url=None,
                  default_response_class=NoStoreJSON)
    app.state.store, app.state.posters, app.state.sightings = store, posters, sd

    # ------------------------------------------------------------ errors (§2.4)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, e: ApiError):
        return NoStoreJSON(e.body(), e.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException):
        if e.status_code in (404, 405):
            return NoStoreJSON(not_found(f"no route {request.method} {request.url.path}").body(), 404)
        code = "invalid_param" if e.status_code == 400 else "internal"
        return NoStoreJSON({"error": {"code": code, "message": str(e.detail)}}, e.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        return NoStoreJSON(invalid(f"{'.'.join(map(str, first.get('loc', ())))}: {first.get('msg', 'invalid')}").body(), 400)

    @app.exception_handler(Exception)
    async def internal(request: Request, e: Exception):
        if is_cut_short(e):  # a media file went away mid-response: the connection closes, as the contract says
            log.info("%s %s: file went away mid-response; connection closed", request.method, request.url.path)
        else:
            log.exception("%s %s failed", request.method, request.url.path)
        return NoStoreJSON({"error": {"code": "internal", "message": f"{type(e).__name__} (see the server log)"}}, 500)

    # ------------------------------------------------------------ auth

    def device(request: Request) -> dict:
        return auth.require(request)

    def media_device(request: Request) -> dict:
        return auth.media(request)

    def desktop(dev: dict = Depends(device)) -> dict:
        """The device `campi ui` created (its id is in ui.db); pairing as "desktop" from elsewhere doesn't count."""
        if dev["id"] != store.get("desktop_device_id"):
            raise not_found("not available")
        return dev

    def signer(dev: dict):
        return lambda path: auth.sign(path, dev["id"])

    def body_obj(payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise invalid("body must be a JSON object")
        return payload

    # ------------------------------------------------------------ pairing and devices (§4.1, §4.2)

    @app.post("/api/pair")
    def pair(payload: Any = Body(None)):
        token, dev = auth.pair(payload)
        log.info("paired device %s (%s, %s)", dev["id"], dev["name"], dev["platform"])
        return NoStoreJSON({"token": token, "device": device_json(dev), "server_name": socket.gethostname(),
                            "api_version": 1}, 201)

    @app.get("/api/devices/me")
    def me(dev: dict = Depends(device)):
        return device_json(dev)

    @app.put("/api/devices/me/push")
    def set_push(payload: Any = Body(None), dev: dict = Depends(device)):
        b = body_obj(payload)
        tok, env, prefs = b.get("apns_token"), b.get("environment"), b.get("prefs")
        if env not in ("sandbox", "production") or not isinstance(prefs, dict) or set(prefs) != set(PUSH_KEYS) \
                or not all(isinstance(v, bool) for v in prefs.values()) \
                or "apns_token" not in b or not (tok is None or (isinstance(tok, str) and APNS_TOKEN_RE.match(tok))):
            raise invalid("need apns_token (hex or null), environment (sandbox | production) and all four prefs")
        store.set_push(dev["id"], tok, env, prefs)
        return device_json(store.device(dev["id"]))

    @app.delete("/api/devices/me")
    def unpair(dev: dict = Depends(device)):
        store.revoke(dev["id"])
        log.info("device %s (%s) unpaired itself", dev["id"], dev["name"])
        return Response(status_code=204)

    # ------------------------------------------------------------ status (§4.3)

    @app.get("/api/status")
    def status(dev: dict = Depends(device)):
        return status_api.build(cfg, signer(dev), probe.get(), lib.latest_id())

    # ------------------------------------------------------------ sightings (§4.4-4.6)

    @app.get("/api/sightings")
    def sightings(request: Request, dev: dict = Depends(device)):
        return sd.list(request.query_params, signer(dev))

    @app.get("/api/sightings/{sid}")
    def sighting(sid: str, dev: dict = Depends(device)):
        return sd.get(sid, signer(dev))

    @app.post("/api/sightings/{sid}/star")
    def star_sighting(sid: str, payload: Any = Body(None), dev: dict = Depends(device)):
        sd.get(sid, signer(dev))
        store.set_star("sighting", sid, flag(payload, "starred"))
        return sd.get(sid, signer(dev))

    @app.post("/api/sightings/{sid}/hide")
    def hide_sighting(sid: str, payload: Any = Body(None), dev: dict = Depends(device)):
        sd.get(sid, signer(dev))
        store.set_hidden(sid, flag(payload, "hidden"))
        return sd.get(sid, signer(dev))

    @app.post("/api/sightings/{sid}/label")
    def label_sighting(sid: str, payload: Any = Body(None), dev: dict = Depends(device)):
        b = body_obj(payload)
        if "label" not in b or not (b["label"] is None or isinstance(b["label"], str)):
            raise invalid("body needs label (a collection label, or null to remove the correction)")
        sd.get(sid, signer(dev))
        if b["label"] is None or not b["label"].strip():
            store.set_label(sid, None)
        else:
            k = sd.resolve_label(b["label"])
            store.set_label(sid, k["label"], k["make"], k["model"])
        return sd.get(sid, signer(dev))

    @app.get("/api/collection")
    def collection(dev: dict = Depends(device)):
        return sd.collection(signer(dev))

    # ------------------------------------------------------------ highlights (§4.7)

    @app.get("/api/highlights")
    def highlights(request: Request, dev: dict = Depends(device)):
        return hl.page(request.query_params, signer(dev))

    # ------------------------------------------------------------ timelapse files (§4.8-4.11)

    @app.get("/api/clips")
    def clips(dev: dict = Depends(device)):
        return lib.clips(signer(dev))

    @app.get("/api/clips/newest")
    def newest_clip(dev: dict = Depends(desktop)):
        """Desktop only: the clip latest.mp4 is a copy of (latest.mp4 itself is never served)."""
        c = lib.newest_clip(signer(dev))
        if c is None:
            raise not_found("no clips yet")
        return c

    @app.get("/api/clips/{cid}")
    def clip(cid: str, dev: dict = Depends(device)):
        return lib.clip(cid, signer(dev))

    @app.post("/api/clips/{cid}/star")
    def star_clip(cid: str, payload: Any = Body(None), dev: dict = Depends(device)):
        value = flag(payload, "starred")
        lib.clip(cid, signer(dev))
        store.set_star("clip", cid, value)
        return lib.clip(cid, signer(dev))

    @app.get("/api/daily")
    def daily(request: Request, dev: dict = Depends(device)):
        return lib.daily_page(request.query_params, signer(dev))

    @app.get("/api/daily/{day}")
    def daily_one(day: str, dev: dict = Depends(device)):
        return lib.daily(day, signer(dev))

    @app.post("/api/daily/{day}/star")
    def star_daily(day: str, payload: Any = Body(None), dev: dict = Depends(device)):
        value = flag(payload, "starred")
        lib.daily(day, signer(dev))
        store.set_star("daily", day, value)
        return lib.daily(day, signer(dev))

    @app.get("/api/archive")
    def archive(dev: dict = Depends(device)):
        return lib.archive(signer(dev))

    @app.get("/api/seek")
    def seek(request: Request, dev: dict = Depends(device)):
        ts = one(request.query_params, "ts")
        if not ts:
            raise invalid("ts is required (RFC 3339 or Unix seconds)")
        return lib.seek(parse_instant(ts), signer(dev))

    # ------------------------------------------------------------ desktop only (not in the contract)

    @app.get("/api/today")
    def today(dev: dict = Depends(desktop)):
        return sd.today()

    @app.get("/api/cards")
    def cards(dev: dict = Depends(desktop)):
        return sd.cards(signer(dev))

    @app.get("/api/activity")
    def activity(request: Request, dev: dict = Depends(desktop)):
        return sd.activity(int_param(request.query_params, "days", 1, 1, 31))

    @app.get("/api/desktop/info")
    def desktop_info(dev: dict = Depends(desktop)):
        return {"rotation": int(cfg.image.rotation), "level_deg": float(cfg.image.level_deg),
                "output_width": int(cfg.output.width), "output_height": int(cfg.output.height),
                "base_fps": int(cfg.output.base_fps), "interp_factor": int(cfg.output.interp_factor),
                "interval_min": int(cfg.render.interval_min), "window_min": int(cfg.render.window_min),
                "clips_hours": float(cfg.retention.clips_hours), "keep_clips_days": float(cfg.sightings.keep_clips_days),
                "sightings_enabled": bool(cfg.sightings.enabled), "reveal_here": in_console_session()}

    @app.post("/api/desktop/reveal")
    def reveal(payload: Any = Body(None), dev: dict = Depends(desktop)):
        """Open Explorer with the file selected. Only from a server running in the desktop session (the Campi
        window reveals files itself when the API runs under the service)."""
        b = body_obj(payload)
        p = media.resolve(cfg, str(b.get("root")), str(b.get("path")), current_part=-1)  # current part may be shown
        if p is None:
            raise not_found("no such file")
        if not in_console_session():
            raise invalid("the API runs in the background service; use the Campi window to show files")
        subprocess.Popen(["explorer.exe", f"/select,{p}"], creationflags=NO_WINDOW)
        return {"shown": str(p)}

    # ------------------------------------------------------------ media + live (§7, §8)

    @app.api_route("/media/{root}/{path:path}", methods=["GET", "HEAD"])
    def media_file(root: str, path: str, request: Request, dev: dict = Depends(media_device)):
        if root == "posters":
            kind, _, name = path.partition("/")
            p = posters.get(kind, name[:-4]) if name.endswith(".jpg") and "/" not in name else None
            if p is None:
                raise not_found(f"no poster {path}")
            return media.serve(request, p, f"posters/{path}", "private, max-age=300")
        p = media.resolve(cfg, root, path, lib.current_part())
        if p is None:
            raise not_found(f"no media {root}/{path}")
        cache = ("private, max-age=86400" if (root == "sightings" and p.suffix == ".jpg") or root == "cards"
                 else "private, max-age=300")  # mesh names change with their source sighting
        return media.serve(request, p, f"{root}/{path}", cache)

    @app.get("/live.mjpg")
    async def live_mjpg(request: Request, dev: dict = Depends(media_device)):
        return await streams.response(request, request.query_params)

    @app.get("/live.jpg")
    def live_jpg(request: Request, dev: dict = Depends(media_device)):
        jpg, ts = snapshot.get(request.query_params)
        return Response(jpg, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Frame-Time": iso(ts)})

    # ------------------------------------------------------------ unknown /api routes: auth first, then 404

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
    def unknown_api(rest: str, request: Request, dev: dict = Depends(device)):
        raise not_found(f"no route {request.method} /api/{rest}")

    # ------------------------------------------------------------ frontend (unauthenticated: no data in it)

    app.mount("/static", RevalidatedStatic(directory=STATIC), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(os.path.join(STATIC, "campi.ico"))

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})

    return app
