"""Service status as data (stdlib only): `campi status` prints it, the UI serves it as /api/status."""
from __future__ import annotations

import shutil
import time

from .config import read_json


def gather(cfg, now: float | None = None) -> dict:
    """Everything `campi status` shows, from the state files the service writes. Read-only."""
    now = now or time.time()
    st = read_json(cfg.paths.state / "status.json", {}) or {}
    alive = bool(st.get("supervisor_pid") and now - st.get("updated", 0) < 30)
    usage = shutil.disk_usage(cfg.paths.frames)
    return {
        "now": now,
        "alive": alive,
        "service": st,
        "capture": read_json(cfg.paths.state / "capture.json", {}) or {},
        "last_clip": read_json(cfg.paths.state / "last_clip.json", {}) or {},
        "last_daily": read_json(cfg.paths.state / "last_daily.json", {}) or {},
        "sightings": sightings_state(cfg, st if alive else {}, now),
        "gaming": gaming_state(cfg, st) if alive else None,
        "render_queue": ({"windows": st.get("render_queue") or 0, "oldest": st.get("render_queue_oldest"),
                          "deferred": bool(st.get("renders_deferred"))} if alive else None),
        "disk": {"free_gb": usage.free / 1e9, "drive": cfg.paths.frames.drive},
        "paths": {"frames": str(cfg.paths.frames), "clips": str(cfg.paths.out), "logs": str(cfg.paths.logs)},
    }


def sightings_state(cfg, st: dict, now: float) -> dict:
    """State of the optional sightings worker; `line` is the one-line text `campi status` prints."""
    if not cfg.sightings.enabled:
        return {"state": "disabled", "line": "disabled"}
    if not cfg.paths.sightings_python.exists():
        return {"state": "not installed", "line": "NOT INSTALLED (run install.ps1 -Sightings)"}
    from . import sightings_db
    ws = read_json(cfg.paths.state / "sightings.json", {}) or {}
    pid = st.get("sightings_pid")
    out = {"pid": pid, "restarts": st.get("sightings_restarts", 0)}
    if st.get("sightings_crash_looping"):
        nxt = st.get("sightings_next_start") or now
        out.update(state="crash-looping", recent_exits=st.get("sightings_recent_exits"),
                   next_try_s=max(0, nxt - now), last_error=ws.get("last_error"))
        line = (f"CRASH-LOOPING ({st.get('sightings_recent_exits')} exits in 15 min; "
                f"next try in {max(0, nxt - now):.0f}s; last error: {ws.get('last_error') or 'see campi logs sightings'})")
    elif st.get("sightings_paused"):
        out.update(state="paused", reason="gaming")
        line = "paused: gaming"
    elif pid and pid in (ws.get("pid"), ws.get("ppid")):
        dev = ("CPU FALLBACK" if ws.get("cpu_fallback") else
               f"{ws.get('device')} {ws.get('device_name') or ''}".strip() if ws.get("device") else "(loading models)")
        out.update(state="running", phase=ws.get("phase"), backend=ws.get("backend", cfg.sightings.backend),
                   device=ws.get("device"), device_name=ws.get("device_name"),
                   cpu_fallback=bool(ws.get("cpu_fallback")), classify_queue=ws.get("classify_queue", 0))
        line = (f"running (pid {pid}, {ws.get('phase')}, restarts {st.get('sightings_restarts', 0)}); "
                f"{ws.get('backend', cfg.sightings.backend)} on {dev}; classify queue {ws.get('classify_queue', 0)}")
        if cfg.sightings.cloud.enabled:
            out["cloud"] = {"today": ws.get("cloud_today"), "cap": cfg.sightings.cloud.cloud_max_per_day,
                            "pending": ws.get("cloud_pending", 0)}
            line += (f"; cloud {ws.get('cloud_today', '?')}/{cfg.sightings.cloud.cloud_max_per_day} today, "
                     f"{ws.get('cloud_pending', 0)} queued")
    elif pid:
        out.update(state="starting")
        line = f"starting (pid {pid})"
    else:
        out.update(state="not running" if st else "service stopped")
        line = "not running" if st else "service stopped"
    summ = sightings_db.summary(cfg.paths.sightings)
    out["summary"] = summ
    if summ:
        last = summ["last"]
        line += (f"; last sighting {sightings_db.local_str(last) if last else 'never'}, "
                 f"today {summ['today']}, total {summ['total']}")
    out["line"] = line
    return out


def gaming_state(cfg, st: dict) -> dict:
    """Gaming detection as the supervisor last saw it; `line` is the text `campi status` prints."""
    g = st.get("gaming") or {}
    if not g:
        return {"line": "not polled yet"}
    if g.get("error"):
        return {**g, "line": f"detection error: {g['error']}"}
    mode = g.get("mode", "auto")
    if g.get("active"):
        since = g.get("since")
        line = f"ACTIVE: {g.get('exe')}" + (f" for {(time.time() - since) / 60:.0f} min" if since else "")
    else:
        line = {"off": "off (campi game off)", "on": "on"}.get(mode, "auto, no game running" if cfg.gaming.auto
                                                                   else "auto detection disabled")
    if g.get("counters") and g["counters"] != "ok":
        line += f"; GPU counters {g['counters']}"
    return {**g, "line": line + f" [mode {mode}]"}
