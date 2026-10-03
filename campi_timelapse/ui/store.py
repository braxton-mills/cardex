"""ui.db: everything the user creates in the UI (stars, hidden sightings, label corrections). The only database the
UI writes; sightings.db is never written. Rollback journal (not WAL) so it can also be ATTACHed read-only."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from ..sightings_db import utc_iso

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (schema_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS stars (
    kind TEXT NOT NULL,              -- sighting | clip | daily
    key TEXT NOT NULL,               -- sighting id, or the video's file name
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS hidden (
    sighting_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS label_overrides (
    sighting_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    make TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);
"""
STAR_KINDS = ("sighting", "clip", "daily")


def ui_home(cfg) -> Path:
    return cfg.paths.data / "ui"


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.generation = 0          # bumped on every write; part of the query cache key
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)
            if con.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0:
                con.execute("INSERT INTO meta (schema_version) VALUES (?)", (SCHEMA_VERSION,))

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        return con

    def _write(self, sql: str, params: tuple) -> None:
        with self._lock:
            con = self._connect()
            try:
                with con:
                    con.execute(sql, params)
            finally:
                con.close()
            self.generation += 1

    def set_star(self, kind: str, key: str, value: bool) -> None:
        if value:
            self._write("INSERT OR IGNORE INTO stars (kind, key, created_at) VALUES (?, ?, ?)",
                        (kind, key, utc_iso(time.time())))
        else:
            self._write("DELETE FROM stars WHERE kind = ? AND key = ?", (kind, key))

    def set_hidden(self, sighting_id: str, value: bool) -> None:
        if value:
            self._write("INSERT OR IGNORE INTO hidden (sighting_id, created_at) VALUES (?, ?)",
                        (sighting_id, utc_iso(time.time())))
        else:
            self._write("DELETE FROM hidden WHERE sighting_id = ?", (sighting_id,))

    def set_label(self, sighting_id: str, label: str | None, make: str | None = None,
                  model: str | None = None) -> None:
        if label:
            self._write("INSERT INTO label_overrides (sighting_id, label, make, model, created_at) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(sighting_id) DO UPDATE SET label = excluded.label, "
                        "make = excluded.make, model = excluded.model, created_at = excluded.created_at",
                        (sighting_id, label, make, model, utc_iso(time.time())))
        else:
            self._write("DELETE FROM label_overrides WHERE sighting_id = ?", (sighting_id,))

    def stars(self, kind: str) -> dict[str, str]:
        """{key: starred_at} for one kind."""
        con = self._connect()
        try:
            return dict(con.execute("SELECT key, created_at FROM stars WHERE kind = ?", (kind,)).fetchall())
        finally:
            con.close()
