"""campi_timelapse CLI: python -m campi_timelapse <command>."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import date

from .config import load_config, read_json, setup_logging, side_log, write_json


def cmd_status(cfg) -> int:
    st = read_json(cfg.paths.state / "status.json", {}) or {}
    cap = read_json(cfg.paths.state / "capture.json", {}) or {}
    clip = read_json(cfg.paths.state / "last_clip.json", {}) or {}
    daily = read_json(cfg.paths.state / "last_daily.json", {}) or {}
    now = time.time()

    def ago(ts):
        return f"{now - ts:.0f}s ago" if ts else "never"

    alive = st.get("supervisor_pid") and now - st.get("updated", 0) < 30
    print(f"service      : {'RUNNING' if alive else 'STOPPED'}"
          + (f" (pid {st['supervisor_pid']}, heartbeat {ago(st.get('updated'))})" if alive else ""))
    if alive:
        print(f"capture      : pid {st.get('capture_pid')}  restarts {st.get('capture_restarts')}")
    print(f"stream       : {'connected' if cap.get('connected') else 'DISCONNECTED'}"
          f"{' to ' + cap['host'] if cap.get('host') else ''}; last frame "
          f"{ago(cap.get('last_frame_ts'))}; saved {cap.get('saved', 0)}, rejected {cap.get('rejected', 0)}, "
          f"reconnects {cap.get('reconnects', 0)}")
    if cap.get("last_error"):
        print(f"last error   : {cap['last_error']}")
    if clip:
        what = clip.get("out") or clip.get("reason") or clip.get("error")
        print(f"last clip    : {clip.get('status')} {ago(clip.get('finished'))}: {what}")
    if alive:
        print(f"next clip    : {st.get('next_clip')}")
    if daily:
        print(f"last daily   : {daily.get('status')} {daily.get('day', '')}: "
              f"{daily.get('out') or daily.get('reason') or daily.get('error')}")
    print(f"sightings    : {sightings_status(cfg, st if alive else {}, now)}")
    print(f"disk free    : {shutil.disk_usage(cfg.paths.frames).free / 1e9:.1f} GB ({cfg.paths.frames.drive})")
    print(f"frames       : {cfg.paths.frames}")
    print(f"clips        : {cfg.paths.out}")
    print(f"logs         : {cfg.paths.logs}")
    return 0 if alive else 3


def sightings_status(cfg, st: dict, now: float) -> str:
    """One-line state of the optional sightings worker (stdlib only: no torch import here)."""
    if not cfg.sightings.enabled:
        return "disabled"
    if not cfg.paths.sightings_python.exists():
        return "NOT INSTALLED (run install.ps1 -Sightings)"
    from . import sightings_db
    ws = read_json(cfg.paths.state / "sightings.json", {}) or {}
    pid = st.get("sightings_pid")
    if st.get("sightings_crash_looping"):
        nxt = st.get("sightings_next_start") or now
        line = (f"CRASH-LOOPING ({st.get('sightings_recent_exits')} exits in 15 min; "
                f"next try in {max(0, nxt - now):.0f}s; last error: {ws.get('last_error') or 'see campi logs sightings'})")
    elif pid and pid in (ws.get("pid"), ws.get("ppid")):
        line = f"running (pid {pid}, {ws.get('phase')}, restarts {st.get('sightings_restarts', 0)})"
    elif pid:
        line = f"starting (pid {pid})"
    else:
        line = "not running" if st else "service stopped"
    summ = sightings_db.summary(cfg.paths.sightings)
    if summ:
        last = summ["last"]
        line += (f"; last sighting {sightings_db.local_str(last) if last else 'never'}, "
                 f"today {summ['today']}, total {summ['total']}")
    return line


def cmd_sightings(cfg, n: int) -> int:
    from . import sightings_db
    con = sightings_db.connect_ro(cfg.paths.sightings)
    if con is None:
        print(f"no sightings database yet ({sightings_db.db_path(cfg.paths.sightings)})")
        return 1
    rows = sightings_db.recent(con, n)[::-1]
    con.close()
    print(sightings_db.format_rows(rows))
    print(f"media: {cfg.paths.sightings}")
    return 0


def cmd_samples(cfg) -> int:
    """Grab one live frame and save before/after rotation samples."""
    import urllib.request

    import cv2
    import numpy as np

    from .capture import iter_jpegs
    from .discover import current_host, with_host
    from .imageproc import level
    resp = urllib.request.urlopen(with_host(cfg.stream.url, current_host(cfg)), timeout=cfg.stream.timeout_s)
    boundary = resp.headers["Content-Type"].split("boundary=", 1)[1].strip().encode()
    data, _ = next(iter_jpegs(resp, boundary))
    resp.close()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    w, h = cfg.output.width, cfg.output.height
    after = level(img, cfg.image.rotation, cfg.image.level_deg, w, h)
    crop = f"{after.shape[1]}x{after.shape[0]}"
    after = cv2.resize(after, (w, h), interpolation=cv2.INTER_AREA if after.shape[1] > w else cv2.INTER_LANCZOS4)
    b, a = cfg.paths.samples / "rotation_before.jpg", cfg.paths.samples / "rotation_after.jpg"
    b.write_bytes(data)
    cv2.imwrite(str(a), after, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"before: {b}  ({img.shape[1]}x{img.shape[0]})\n"
          f"after : {a}  (rotation {cfg.image.rotation}, level {cfg.image.level_deg} deg, "
          f"crop {crop} -> {w}x{h})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="campi_timelapse")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="supervisor: capture + scheduled renders + housekeeping")
    rn.add_argument("--foreground", action="store_true", help="manual run in a console (ignores `campi stop`)")
    sub.add_parser("capture", help="capture only (foreground)")
    rc = sub.add_parser("render-clip", help="render the last N minutes")
    rc.add_argument("--minutes", type=float)
    rc.add_argument("--end", type=float, help="window end (epoch seconds); default now")
    rd = sub.add_parser("render-daily")
    rd.add_argument("--date", type=date.fromisoformat)
    sub.add_parser("housekeep")
    sub.add_parser("archive", help="append any clips not yet in the long archive video (also backfills)")
    sub.add_parser("status")
    sub.add_parser("samples", help="save before/after rotation samples from the live stream")
    sl = sub.add_parser("sightings", help="list the last N vehicle sightings")
    sl.add_argument("n", nargs="?", type=int, default=20)
    sw = sub.add_parser("sightings-worker", help="vehicle sightings worker (run by the supervisor; sightings venv)")
    sw.add_argument("--check", action="store_true", help="load models, report the GPU, exit")
    sr = sub.add_parser("sightings-record", help="save the raw stream to a file for sightings-test")
    sr.add_argument("seconds", type=float)
    sr.add_argument("--out")
    stt = sub.add_parser("sightings-test", help="run the sightings pipeline on a file into a separate test DB")
    stt.add_argument("--source", required=True)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "samples":
        return cmd_samples(cfg)
    if args.cmd == "sightings":
        return cmd_sightings(cfg, args.n)

    log_name = {"run": "supervisor", "render-clip": "render", "render-daily": "daily",
                "sightings-worker": "sightings"}.get(args.cmd, args.cmd)
    log = setup_logging(cfg, log_name)
    side_log(cfg, "gaps")
    try:
        if args.cmd == "run":
            from .supervisor import Supervisor
            return Supervisor(cfg).run(foreground=args.foreground)
        if args.cmd == "capture":
            from . import capture
            capture.run(cfg)
            return 0
        if args.cmd == "render-clip":
            from .render import render_clip
            res = render_clip(cfg, args.end, args.minutes)
        elif args.cmd == "render-daily":
            from .render import render_daily
            res = render_daily(cfg, args.date)
        elif args.cmd == "archive":
            from .archive import append_pending
            res = append_pending(cfg)
        elif args.cmd == "sightings-worker":
            from .sightings import run_worker
            return run_worker(cfg, check_only=args.check)
        elif args.cmd == "sightings-record":
            from pathlib import Path
            from .sightings import record
            res = record(cfg, args.seconds, Path(args.out) if args.out else None)
        elif args.cmd == "sightings-test":
            from pathlib import Path
            from . import sightings_db
            from .sightings import run_test
            rows = run_test(cfg, Path(args.source))
            print(sightings_db.format_rows(rows))
            for r in rows:
                print(json.dumps(dict(r)))
            print(f"test db + media: {cfg.paths.data / 'sightings-test'}")
            return 0
        elif args.cmd == "housekeep":
            from . import housekeeping
            housekeeping.run(cfg)
            return 0
        if sys.stdout is not None:
            print(json.dumps(res, indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        log.exception("%s failed", args.cmd)
        if args.cmd in ("render-clip", "render-daily"):
            state = "last_clip.json" if args.cmd == "render-clip" else "last_daily.json"
            write_json(cfg.paths.state / state, {"status": "error", "error": f"{type(e).__name__}: {e}",
                                                 "finished": time.time()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
