"""Sightings SQLite store (stdlib only, so status / housekeeping / the list command work from the service venv).

One row per vehicle pass. Times are ISO 8601 UTC; media paths are relative to the sightings folder (posix '/').
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("sightings")

SCHEMA_VERSION = 2
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
           "frame_path", "clip_path", "synced_at",
           "year_range", "color", "source", "siglip_label", "siglip_confidence")  # v2
V2_COLUMNS = (("year_range", "TEXT"), ("color", "TEXT"), ("source", "TEXT"), ("siglip_label", "TEXT"),
              ("siglip_confidence", "REAL"))
V2_TABLES = """
CREATE TABLE IF NOT EXISTS cloud_queue (
    sighting_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try_at TEXT NOT NULL,
    last_error TEXT,
    done_at TEXT,
    result TEXT
);
CREATE INDEX IF NOT EXISTS cloud_queue_pending ON cloud_queue (done_at, next_try_at);
CREATE TABLE IF NOT EXISTS discovered_labels (
    label TEXT PRIMARY KEY,
    make TEXT,
    model TEXT,
    first_seen TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1
);
"""


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
        con.execute("INSERT INTO schema_version (version) VALUES (1)")
    con.commit()
    migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> None:
    """v1 -> v2 in one transaction (idempotent; safe on the live DB): SigLIP-vs-cloud columns, cloud queue,
    discovered labels. Existing rows are backfilled as SigLIP answers."""
    if con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    have = {r[1] for r in con.execute("PRAGMA table_info(sightings)")}
    con.execute("BEGIN IMMEDIATE")
    try:
        for name, typ in V2_COLUMNS:
            if name not in have:
                con.execute(f"ALTER TABLE sightings ADD COLUMN {name} {typ}")
        con.execute("UPDATE sightings SET source = 'siglip', siglip_label = label, siglip_confidence = confidence "
                    "WHERE source IS NULL")
        for stmt in V2_TABLES.split(";"):
            if stmt.strip():
                con.execute(stmt)
        con.execute("UPDATE schema_version SET version = 2")
        con.commit()
    except BaseException:
        con.rollback()
        raise
    log.info("sightings database migrated to schema v2")


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
        cloud = "source" in r.keys() and r["source"] == "cloud"
        flags = (("U" if r["unsure"] else "") + ("S" if r["stationary"] else "") + ("C" if r["clip_path"] else "")
                 + ("G" if cloud else ""))
        conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else ""
        out.append(f"{local_str(r['started_at']):<19}  {r['kind']:<7} {r['yolo_class'] or '':<10} "
                   f"{(r['label'] or '')[:28]:<28} {conf:>5}  {flags:<6} {r['direction'] or '':<3} "
                   f"{r['track_frames'] or 0:>6}")
    out.append("flags: U = unsure, S = stationary, C = has clip, G = decided by the cloud")
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


# ---------------------------------------------------------------- cloud second opinion (v2)

def local_day_bounds_utc(d: date | None = None) -> tuple[str, str]:
    start = datetime.combine(d or date.today(), dtime.min).astimezone()
    return utc_iso(start.timestamp()), utc_iso((start + timedelta(days=1)).timestamp())


def cloud_calls_today(con: sqlite3.Connection) -> int:
    """Requests actually sent today (local day): resets at local midnight."""
    lo, hi = local_day_bounds_utc()
    return con.execute("SELECT COUNT(*) FROM cloud_queue WHERE done_at >= ? AND done_at < ? "
                       "AND (last_error IS NULL OR last_error != 'cap')", (lo, hi)).fetchone()[0]


def enqueue_cloud(con: sqlite3.Connection, sighting_id: str, now: float | None = None) -> None:
    now = now or time.time()
    con.execute("INSERT OR IGNORE INTO cloud_queue (sighting_id, created_at, next_try_at) VALUES (?, ?, ?)",
                (sighting_id, utc_iso(now), utc_iso(now)))
    con.commit()


def next_cloud_job(con: sqlite3.Connection, now: float | None = None):
    return con.execute("SELECT q.sighting_id, q.attempts, s.crop_path, s.label, s.runner_ups, s.confidence, "
                       "s.started_at FROM cloud_queue q JOIN sightings s ON s.id = q.sighting_id "
                       "WHERE q.done_at IS NULL AND q.next_try_at <= ? ORDER BY q.created_at LIMIT 1",
                       (utc_iso(now or time.time()),)).fetchone()


def cloud_pending(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM cloud_queue WHERE done_at IS NULL").fetchone()[0]


def retry_cloud_job(con: sqlite3.Connection, sighting_id: str, error: str, delay_s: float) -> None:
    con.execute("UPDATE cloud_queue SET attempts = attempts + 1, last_error = ?, next_try_at = ? "
                "WHERE sighting_id = ?", (error, utc_iso(time.time() + delay_s), sighting_id))
    con.commit()


def finish_cloud_job(con: sqlite3.Connection, sighting_id: str, result: dict | None, error: str | None = None,
                     update: dict | None = None) -> None:
    """Close a job; with `update`, overwrite the row's answer (label/make/model/...) in the same transaction."""
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("UPDATE cloud_queue SET done_at = ?, last_error = ?, result = ?, attempts = attempts + 1 "
                    "WHERE sighting_id = ?",
                    (utc_iso(time.time()), error, json.dumps(result) if result is not None else None, sighting_id))
        if update:
            cols = ", ".join(f"{k} = ?" for k in update)
            con.execute(f"UPDATE sightings SET {cols} WHERE id = ?", [*update.values(), sighting_id])
        con.commit()
    except BaseException:
        con.rollback()
        raise


def record_discovered(con: sqlite3.Connection, label: str, make: str | None, model: str | None,
                      seen_iso: str) -> None:
    con.execute("INSERT INTO discovered_labels (label, make, model, first_seen) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(label) DO UPDATE SET count = count + 1", (label, make, model, seen_iso))
    con.commit()


def label_seen_before(con: sqlite3.Connection, label: str) -> bool:
    return con.execute("SELECT 1 FROM sightings WHERE label = ? OR siglip_label = ? LIMIT 1",
                       (label, label)).fetchone() is not None
