"""The UI's HTTP API (JSON) and static frontend. The same API is meant for other clients (an iPhone app later), so it
returns data, not UI shapes: times are ISO 8601 (`*_at` UTC as stored, `*_local` with the local offset), lists are
{items, next_cursor}. Interactive docs at /api/docs."""
from __future__ import annotations

import bisect
import copy
import ipaddress
import logging
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..archive import CLIP_RE, clip_start
from ..config import NO_WINDOW
from ..sightings_db import utc_iso
from ..status import gather
from . import live as live_mod
from . import media
from .library import DAILY_RE, Library
from .sightings_data import Sightings, to_ts
from .store import Store, ui_home

log = logging.getLogger("ui")
STATIC = Path(__file__).parent / "static"
HIGHLIGHT_TYPES = ("new_catch", "rare", "busy_window", "daily", "starred")


class Flag(BaseModel):
    value: bool


class LabelIn(BaseModel):
    label: str | None = None


class RevealIn(BaseModel):
    root: str
    path: str


def is_loopback(host: str | None) -> bool:
    try:
        return host in ("localhost",) or ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def parse_ts(v: str) -> float:
    try:
        return float(v)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, "ts must be epoch seconds or ISO 8601") from None
    return dt.timestamp()


def create_app(cfg, bind_host: str = "127.0.0.1") -> FastAPI:
    store = Store(ui_home(cfg) / "ui.db")
    sd = Sightings(cfg, store)
    lib = Library(cfg, store)
    app = FastAPI(title="Campi", version="1", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
                  description="Read-only view of the Campi timelapse service, plus the user's stars, hidden "
                              "sightings and label corrections (stored in CampiTimelapse\\ui\\ui.db).")

    starts_cache: dict = {}

    def counts(lo: float, hi: float) -> int | None:
        """Visible sightings in [lo, hi) (None without a sightings database); one query covers every clip."""
        if not sd.exists():
            return None
        key = (sd._signature(), int(lo // 86400))
        if starts_cache.get("key") != key:
            day = int(lo // 86400) * 86400
            starts_cache.update(key=key, starts=sd.starts_between(day - 86400, day + 2 * 86400))
        s = starts_cache["starts"]
        return bisect.bisect_left(s, hi) - bisect.bisect_left(s, lo)

    def sighting_or_404(sid: str) -> dict:
        d = sd.get(sid)
        if d is None:
            raise HTTPException(404, "no such sighting")
        return d

    # ------------------------------------------------------------ status

    @app.get("/api/status", tags=["status"])
    def status():
        """Same data as `campi status`, structured. `sightings.line` / `gaming.line` are the CLI's text."""
        g = gather(cfg)
        g["sightings_enabled"] = bool(cfg.sightings.enabled)
        g["sightings_db"] = sd.exists()
        g["config"] = {"base_fps": cfg.output.base_fps, "interp_factor": cfg.output.interp_factor,
                       "window_min": cfg.render.window_min, "interval_min": cfg.render.interval_min,
                       "clips_hours": cfg.retention.clips_hours, "keep_clips_days": cfg.sightings.keep_clips_days,
                       "rotation": cfg.image.rotation, "level_deg": cfg.image.level_deg,
                       "output_width": cfg.output.width, "output_height": cfg.output.height}
        return g

    # ------------------------------------------------------------ sightings

    @app.get("/api/sightings", tags=["sightings"])
    def sightings(from_: str | None = Query(None, alias="from", description="local date YYYY-MM-DD"),
                  to: str | None = Query(None, description="local date YYYY-MM-DD (inclusive)"),
                  make: str | None = None, class_: str | None = Query(None, alias="class"),
                  source: str | None = Query(None, description="siglip | cloud | user"),
                  label: str | None = None, hide_unsure: bool = False, hide_stationary: bool = False,
                  starred: bool = False, include_hidden: bool = False, cursor: str | None = None,
                  limit: int = Query(60, ge=1, le=500)):
        """Newest first. Page with `cursor` = the previous page's `next_cursor`."""
        f = {"from": from_, "to": to, "make": make, "class": class_, "source": source, "label": label,
             "hide_unsure": hide_unsure, "hide_stationary": hide_stationary, "starred": starred,
             "include_hidden": include_hidden}
        try:
            return with_seek(sd.list(f, cursor, limit))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    def with_seek(page: dict) -> dict:
        for s in page["items"]:
            add_seek_ts(s)
        return page

    def add_seek_ts(s: dict) -> dict:
        """Middle of the pass: where /api/seek should land for this sighting."""
        a = to_ts(s["started_at"])
        s["seek_ts"] = round((a + to_ts(s["ended_at"])) / 2 if s.get("ended_at") else a, 3)
        return s

    @app.get("/api/sightings/{sid}", tags=["sightings"])
    def sighting(sid: str):
        return add_seek_ts(sighting_or_404(sid))

    @app.post("/api/sightings/{sid}/star", tags=["sightings"])
    def star_sighting(sid: str, body: Flag):
        sighting_or_404(sid)
        store.set_star("sighting", sid, body.value)
        return add_seek_ts(sighting_or_404(sid))

    @app.post("/api/sightings/{sid}/hide", tags=["sightings"])
    def hide_sighting(sid: str, body: Flag):
        """Hidden sightings are left out of every list, count, highlight and the collection."""
        sighting_or_404(sid)
        store.set_hidden(sid, body.value)
        return add_seek_ts(sighting_or_404(sid))

    @app.post("/api/sightings/{sid}/label", tags=["sightings"])
    def label_sighting(sid: str, body: LabelIn):
        """Correct the label (`null` or "" clears the correction). Applied everywhere, including counts."""
        sighting_or_404(sid)
        text = (body.label or "").strip()
        if text:
            name, mk, md = sd.resolve_label(text)
            store.set_label(sid, name, mk, md)
        else:
            store.set_label(sid, None)
        return add_seek_ts(sighting_or_404(sid))

    @app.get("/api/collection", tags=["sightings"])
    def collection():
        """Every label in sightings_labels.txt (caught or not) plus discovered labels; tiers from counts."""
        return sd.collection()

    @app.get("/api/today", tags=["sightings"])
    def today():
        return sd.today()

    # ------------------------------------------------------------ highlights

    @app.get("/api/highlights", tags=["highlights"])
    def highlights(type: str | None = Query(None, description="comma list: " + ", ".join(HIGHLIGHT_TYPES)),
                   before: str | None = Query(None, description="only items with `at` before this (cursor)"),
                   limit: int = Query(50, ge=1, le=500)):
        want = set(type.split(",")) if type else set(HIGHLIGHT_TYPES)
        items = copy.deepcopy(sd.highlight_items())
        clip_stars = store.stars("clip")
        for it in items:
            if it.get("sighting"):
                add_seek_ts(it["sighting"])
            if it["type"] == "busy_window":
                start = to_ts(it["window"]["start_at"])
                p = cfg.paths.out / f"campi_{datetime.fromtimestamp(start):%Y-%m-%d_%H%M}.mp4"
                it["clip"] = lib.clip_info(p, start, clip_stars, counts) if p.is_file() else None
                it["seek_ts"] = start
        daily_stars = store.stars("daily")
        for d in lib.daily():
            end = datetime.combine(date.fromisoformat(d["day"]) + timedelta(days=1), datetime.min.time()).timestamp()
            types = ["daily"] + (["starred"] if d["name"] in daily_stars else [])
            items.append({"type": "daily", "types": types, "at": utc_iso(end), "title": f"Day {d['day']}",
                          "daily": d})
        for name in clip_stars:
            m = CLIP_RE.match(name)
            if not m:
                continue
            p = cfg.paths.out / name
            start = clip_start(p)
            info = lib.clip_info(p, start, clip_stars, counts) if p.is_file() else None
            items.append({"type": "starred", "types": ["starred"], "at": utc_iso(start),
                          "title": f"Starred clip {datetime.fromtimestamp(start):%Y-%m-%d %H:%M}", "clip": info,
                          "seek_ts": start})
        items = [i for i in items if want & set(i["types"]) and (not before or i["at"] < before)]
        items.sort(key=lambda i: i["at"], reverse=True)
        page = items[:limit]
        return {"items": page, "next_cursor": page[-1]["at"] if len(items) > limit else None}

    # ------------------------------------------------------------ timelapse files

    @app.get("/api/clips", tags=["timelapse"])
    def clips(hours: float = Query(24, gt=0, le=24 * 30)):
        """10-minute clips whose window started in the last `hours`, newest first."""
        return {"items": lib.clips(hours, counts), "next_cursor": None}

    @app.get("/api/clips/newest", tags=["timelapse"])
    def newest_clip():
        """The dated clip latest.mp4 currently copies (latest.mp4 itself is never served)."""
        c = lib.newest_clip(counts)
        if not c:
            raise HTTPException(404, "no clips yet")
        return c

    @app.get("/api/daily", tags=["timelapse"])
    def daily():
        return {"items": lib.daily(), "next_cursor": None}

    @app.get("/api/archive", tags=["timelapse"])
    def archive():
        """Archive parts; the current part (still being appended to) has url null and can't be played."""
        return {"items": lib.archive(), "next_cursor": None}

    @app.get("/api/seek", tags=["timelapse"])
    def seek(ts: str = Query(..., description="epoch seconds or ISO 8601")):
        """Video + offset showing wall-clock time `ts`: the 10-minute clip while it exists (exact), else that day's
        daily video (approximate). 404 if neither exists."""
        r = lib.seek(parse_ts(ts))
        if not r:
            raise HTTPException(404, "no clip or daily video covers that time")
        return r

    @app.post("/api/videos/{kind}/{name}/star", tags=["timelapse"])
    def star_video(kind: str, name: str, body: Flag):
        """Star a 10-minute clip (`kind` clips) or a daily video (`daily`); starred items appear in highlights."""
        if kind == "clips" and CLIP_RE.match(name):
            store.set_star("clip", name, body.value)
        elif kind == "daily" and DAILY_RE.match(name):
            store.set_star("daily", name, body.value)
        else:
            raise HTTPException(404, "unknown video")
        return {"kind": kind, "name": name, "starred": body.value}

    @app.post("/api/desktop/reveal", tags=["desktop"])
    def reveal(body: RevealIn, request: Request):
        """Desktop only: open Explorer with the file selected. Only for loopback clients of a loopback server."""
        if not (is_loopback(bind_host) and is_loopback(request.client.host if request.client else None)):
            raise HTTPException(501, "only available on this PC")
        p = media.resolve(cfg, body.root, body.path, current_part=-1)  # the current archive part may be shown
        if p is None:
            raise HTTPException(404, "not found")
        subprocess.Popen(["explorer.exe", f"/select,{p}"], creationflags=NO_WINDOW)
        return {"shown": str(p)}

    # ------------------------------------------------------------ media + live

    @app.api_route("/media/{root}/{path:path}", methods=["GET", "HEAD"], tags=["media"])
    def media_file(root: str, path: str, request: Request):
        """Videos and images (HTTP Range supported). Only service output under the configured roots."""
        p = media.resolve(cfg, root, path, lib.current_part())
        if p is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return media.serve(request, p, f"{root}/{path}")

    @app.get("/live.mjpg", tags=["media"])
    async def live(request: Request):
        """The camera's live MJPEG stream (one upstream connection per viewer, closed when the viewer leaves)."""
        return await live_mod.live(request, cfg)

    # ------------------------------------------------------------ frontend

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        """Always revalidate the frontend (cheap 304s): after an update, a cached old lib.js next to a new view
        module would break the module imports."""
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "campi.ico")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app
