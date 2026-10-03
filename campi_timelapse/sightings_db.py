"""Sightings SQLite store (stdlib only, so status / housekeeping / the list command work from the service venv).

One row per vehicle pass. Times are ISO 8601 UTC; media paths are relative to the sightings folder (posix '/').
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("sightings")

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sightings (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    yolo_class TEXT,
    label TEXT,
    make TEXT,
    model TEXT,
    confidence REAL,
    runner_ups TEXT,
    unsure INTEGER,
    stationary INTEGER,
    direction TEXT,
    track_frames INTEGER,
    max_box_px INTEGER,
    crop_path TEXT,
    frame_path TEXT,
    clip_path TEXT,
    synced_at TEXT NULL
);
CREATE INDEX IF NOT EXISTS sightings_started_at ON sightings (started_at);
"""
COLUMNS = ("id", "kind", "started_at", "ended_at", "yolo_class", "label", "make", "model", "confidence",
           "runner_ups", "unsure", "stationary", "direction", "track_frames", "max_box_px", "crop_path",
           "frame_path", "clip_path", "synced_at")


def db_path(root: Path) -> Path:
    return root / "sightings.db"


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


def local_str(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def connect(root: Path) -> sqlite3.Connection:
    """Read-write connection; creates the database (WAL) and schema on first use."""
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path(root), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        con.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    con.commit()
    return con


def connect_ro(root: Path) -> sqlite3.Connection | None:
    p = db_path(root)
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def insert(con: sqlite3.Connection, row: dict) -> None:
    cols = [c for c in COLUMNS if c in row]
    con.execute(f"INSERT INTO sightings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [row[c] for c in cols])
    con.commit()


def recent(con: sqlite3.Connection, n: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM sightings ORDER BY started_at DESC LIMIT ?", (n,)).fetchall()


def summary(root: Path) -> dict | None:
    """Last sighting time and today's count (local day), for `campi status`."""
    con = connect_ro(root)
    if con is None:
        return None
    try:
        start = datetime.combine(date.today(), dtime.min).astimezone()
        end = start + timedelta(days=1)
        today = con.execute("SELECT COUNT(*) FROM sightings WHERE started_at >= ? AND started_at < ?",
                            (utc_iso(start.timestamp()), utc_iso(end.timestamp()))).fetchone()[0]
        last = con.execute("SELECT MAX(started_at) FROM sightings").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
        return {"today": today, "last": last, "total": total}
    finally:
        con.close()


def format_rows(rows) -> str:
    head = f"{'time (local)':<19}  {'kind':<7} {'class':<10} {'label':<28} {'conf':>5}  {'flags':<6} {'dir':<3} {'frames':>6}"
    out = [head, "-" * len(head)]
    for r in rows:
        flags = ("U" if r["unsure"] else "") + ("S" if r["stationary"] else "") + ("C" if r["clip_path"] else "")
        conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else ""
        out.append(f"{local_str(r['started_at']):<19}  {r['kind']:<7} {r['yolo_class'] or '':<10} "
                   f"{(r['label'] or '')[:28]:<28} {conf:>5}  {flags:<6} {r['direction'] or '':<3} "
                   f"{r['track_frames'] or 0:>6}")
    out.append("flags: U = unsure, S = stationary, C = has clip")
    return "\n".join(out)


def expire_clips(root: Path, keep_days: float, now: float | None = None) -> int:
    """Delete clips older than keep_days. clip_path is cleared (committed) before the file is deleted,
    so a crash in between leaves an orphan file, never a row pointing at a missing clip."""
    now = now or time.time()
    cutoff = utc_iso(now - keep_days * 86400)
    con = sqlite3.connect(db_path(root), timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        rows = con.execute("SELECT id, clip_path FROM sightings WHERE clip_path IS NOT NULL AND started_at < ?",
                           (cutoff,)).fetchall()
        if rows:
            con.executemany("UPDATE sightings SET clip_path = NULL WHERE id = ?", [(r[0],) for r in rows])
            con.commit()
        for _, rel in rows:
            (root / rel).unlink(missing_ok=True)
        live = {r[0] for r in con.execute("SELECT clip_path FROM sightings WHERE clip_path IS NOT NULL")}
    finally:
        con.close()
    orphans = 0  # clips no row points at (crash between the two steps above, or an aborted write)
    for p in root.glob("*/*.mp4"):
        rel = p.relative_to(root).as_posix()
        if rel not in live and p.stat().st_mtime < now - keep_days * 86400:
            p.unlink(missing_ok=True)
            orphans += 1
    if rows or orphans:
        log.info("expired %d sighting clips older than %g days (%d orphans)", len(rows), keep_days, orphans)
    return len(rows)
