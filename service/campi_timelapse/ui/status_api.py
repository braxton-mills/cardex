"""GET /api/status (contract §5.1): the same state files `campi status` reads (status.gather), in the contract's
shape. The "reserved" fields come from what the service already persists: backend/device/CPU fallback/classify
queue/cloud calls in state\\sightings.json, gaming and the render queue in status.json and render_queue.json."""
from __future__ import annotations

import socket
from datetime import datetime
from pathlib import Path

from .. import sightings_db
from ..archive import CLIP_RE
from ..config import read_json
from ..status import gather
from .contract import API_VERSION, iso, iso_from_utc

SIGHTINGS_STATES = {"disabled": "disabled", "not installed": "not_installed", "service stopped": "service_stopped",
                    "not running": "not_running", "starting": "starting", "running": "running",
                    "crash-looping": "crash_looping", "paused": "paused_gaming"}
PHASES = ("loading", "disconnected", "running", "paused_dark", "error")


def _local_naive(s) -> float | None:
    """status.json's next_clip / render_queue_oldest are naive local ISO strings."""
    try:
        return datetime.fromisoformat(s).timestamp() if s else None
    except (TypeError, ValueError):
        return None


def _render_result(d: dict, clip: bool) -> dict | None:
    if not d or d.get("status") not in ("ok", "skipped", "error"):
        return None
    name = Path(d.get("out") or "").name
    return {"status": d["status"], "finished_at": iso(d.get("finished")),
            "clip_id": name[len("campi_"):-len(".mp4")] if clip and d["status"] == "ok" and CLIP_RE.match(name) else None,
            "day": d.get("day") if not clip else None,
            "detail": d.get("reason") if d["status"] == "skipped" else (d.get("error") if d["status"] == "error" else None)}


def build(cfg, signer, live_available: bool, latest_clip_id: str | None) -> dict:
    g = gather(cfg)
    st, alive, cap = g["service"], g["alive"], g["capture"]
    state_dir = cfg.paths.state

    service = {"state": "running" if alive else "stopped", "pid": st.get("supervisor_pid") if alive else None,
               "started_at": iso(st.get("started")) if alive else None, "heartbeat_at": iso(st.get("updated")),
               "disabled": (state_dir / "disabled").exists()}
    capture = {"connected": bool(cap.get("connected")), "host": cap.get("host") or None,
               "last_frame_at": iso(cap.get("last_frame_ts")), "saved": int(cap.get("saved") or 0),
               "rejected": int(cap.get("rejected") or 0), "reconnects": int(cap.get("reconnects") or 0),
               "restarts": int(st.get("capture_restarts") or 0) if alive else None,
               "last_error": cap.get("last_error") or None}

    queue = None
    if alive:
        boundaries = sorted(b for b in (read_json(state_dir / "render_queue.json", []) or [])
                            if isinstance(b, (int, float)))
        deferred = bool(st.get("renders_deferred"))
        queue = {"length": len(boundaries),
                 "oldest_window_start": iso(boundaries[0] - int(cfg.render.window_min) * 60) if boundaries else None,
                 "deferred": deferred, "deferred_reason": "gaming" if deferred else None}
    clips = {"last": _render_result(g["last_clip"], True), "next_at": iso(_local_naive(st.get("next_clip"))) if alive else None,
             "running": bool(st.get("clip_running")) if alive else None, "queue": queue}
    daily = {"last": _render_result(g["last_daily"], False),
             "running": bool(st.get("daily_running")) if alive else None}

    s = g["sightings"]
    state = SIGHTINGS_STATES.get(s.get("state"), "not_running")
    enabled = bool(cfg.sightings.enabled)
    summ = s.get("summary") if "summary" in s else sightings_db.summary(cfg.paths.sightings)
    ws = (read_json(state_dir / "sightings.json", {}) or {}) if enabled else {}
    running = state == "running"
    sightings = {
        "enabled": enabled, "state": state,
        "phase": ws.get("phase") if running and ws.get("phase") in PHASES else None,
        "restarts": int(st.get("sightings_restarts") or 0) if enabled and alive else None,
        "last_error": (ws.get("last_error") or None) if enabled else None,
        "next_retry_at": iso(st.get("sightings_next_start")) if state == "crash_looping" else None,
        "has_history": sightings_db.db_path(cfg.paths.sightings).exists(),
        "today": int((summ or {}).get("today") or 0), "total": int((summ or {}).get("total") or 0),
        "last_sighting_at": iso_from_utc((summ or {}).get("last")),
        "backend": ws.get("backend") if running and ws.get("backend") in ("openvino", "cuda") else None,
        "device": (ws.get("device_name") or ws.get("device")) if running else None,
        "cpu_fallback": bool(ws.get("cpu_fallback")) if running and "cpu_fallback" in ws else None,
        "classify_queue": int(ws.get("classify_queue") or 0) if running and "classify_queue" in ws else None,
        "cloud": {"enabled": bool(cfg.sightings.cloud.enabled), "calls_today": int(ws.get("cloud_today") or 0),
                  "cap": int(cfg.sightings.cloud.cloud_max_per_day)} if enabled else None,
    }

    gaming = None
    gm = st.get("gaming") if alive else None
    if gm:
        gaming = {"mode": gm.get("mode") if gm.get("mode") in ("auto", "on", "off") else "auto",
                  "active": bool(gm.get("active")), "exe": gm.get("exe") or None, "since": iso(gm.get("since")),
                  "detection": "counters" if gm.get("counters") == "ok" else "path_only",
                  "renders_deferred": bool(st.get("renders_deferred"))}

    return {
        "api_version": API_VERSION, "server_name": socket.gethostname(), "server_time": iso(g["now"]),
        "service": service, "capture": capture, "clips": clips, "daily": daily, "sightings": sightings,
        "gaming": gaming, "disk": {"free_gb": round(g["disk"]["free_gb"], 1), "drive": g["disk"]["drive"]},
        "latest_clip_id": latest_clip_id,
        "live": {"available": live_available, "mjpeg": signer("/live.mjpg"), "snapshot": signer("/live.jpg"),
                 "rotation": int(cfg.image.rotation), "max_viewers": int(cfg.api.max_live_viewers)},
    }
