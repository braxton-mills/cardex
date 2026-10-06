"""ui.db: everything the API and its clients create (stars, hidden sightings, label corrections, paired devices,
pairing codes, the URL-signing secret, push bookkeeping). The only database the API writes; sightings.db is never
written. Rollback journal (not WAL) so it can also be ATTACHed read-only. Several processes use it (the API,
`campi ui`, `campi pair` / `campi devices`), so nothing here is cached across calls."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from ..sightings_db import utc_iso

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (schema_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS stars (
    kind TEXT NOT NULL,              -- sighting | clip | daily
    key TEXT NOT NULL,               -- sighting id, clip id (2026-10-03_1410) or day (2026-10-02)
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
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,             -- dev_ + 16 base32 chars
    name TEXT NOT NULL,
    platform TEXT NOT NULL,          -- ios | desktop | other
    token_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT,
    apns_token TEXT,
    apns_env TEXT,                   -- sandbox | production
    prefs TEXT NOT NULL              -- JSON PushPrefs
);
CREATE TABLE IF NOT EXISTS pair_codes (
    code TEXT PRIMARY KEY,           -- 8 Crockford base32 chars, no dash
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pushes (
    sighting_id TEXT NOT NULL,       -- or service:<alert>:<incident start> for service alerts
    type TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (sighting_id, type)
);
"""
STAR_KINDS = ("sighting", "clip", "daily", "custom")
DEFAULT_PREFS = {"new_catch": True, "rare": False, "discovered": True, "service_alerts": True}
DEVICE_COLS = ("id", "name", "platform", "created_at", "last_seen_at", "revoked_at", "apns_token", "apns_env", "prefs")


def ui_home(cfg) -> Path:
    return cfg.paths.data / "ui"


def now_iso() -> str:
    return utc_iso(time.time())


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}   # device id -> last time last_seen_at was written (at most once a minute)
        path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            with con:
                con.executescript(SCHEMA)
                if con.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0:
                    con.execute("INSERT INTO meta (schema_version) VALUES (?)", (1,))
            self._migrate(con)
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        return con

    def _migrate(self, con) -> None:
        """v1 (UI) -> v2 (contract): devices, pairing, kv, pushes; star keys become contract ids."""
        if con.execute("SELECT MAX(schema_version) FROM meta").fetchone()[0] >= SCHEMA_VERSION:
            return
        con.execute("BEGIN IMMEDIATE")
        try:
            for stmt in SCHEMA_V2.split(";"):
                if stmt.strip():
                    con.execute(stmt)
            # campi_2026-10-03_1410.mp4 -> 2026-10-03_1410; campi_daily_2026-10-02.mp4 -> 2026-10-02
            con.execute("UPDATE OR REPLACE stars SET key = substr(key, 7, length(key) - 10) "
                        "WHERE kind = 'clip' AND key LIKE 'campi\\_%.mp4' ESCAPE '\\'")
            con.execute("UPDATE OR REPLACE stars SET key = substr(key, 13, 10) "
                        "WHERE kind = 'daily' AND key LIKE 'campi\\_daily\\_%.mp4' ESCAPE '\\'")
            con.execute("UPDATE meta SET schema_version = ?", (SCHEMA_VERSION,))
            con.commit()
        except BaseException:
            con.rollback()
            raise

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            con = self._connect()
            try:
                with con:
                    return con.execute(sql, params).rowcount
            finally:
                con.close()

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        con = self._connect()
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def signature(self) -> tuple:
        """Changes on every commit by any process (rollback journal: the main file is rewritten)."""
        try:
            s = self.path.stat()
            return s.st_mtime_ns, s.st_size
        except OSError:
            return None, None

    # ------------------------------------------------------------ stars, hidden, corrections

    def set_star(self, kind: str, key: str, value: bool) -> None:
        if value:
            self._write("INSERT OR IGNORE INTO stars (kind, key, created_at) VALUES (?, ?, ?)", (kind, key, now_iso()))
        else:
            self._write("DELETE FROM stars WHERE kind = ? AND key = ?", (kind, key))

    def set_hidden(self, sighting_id: str, value: bool) -> None:
        if value:
            self._write("INSERT OR IGNORE INTO hidden (sighting_id, created_at) VALUES (?, ?)", (sighting_id, now_iso()))
        else:
            self._write("DELETE FROM hidden WHERE sighting_id = ?", (sighting_id,))

    def set_label(self, sighting_id: str, label: str | None, make: str | None = None,
                  model: str | None = None) -> None:
        if label:
            self._write("INSERT INTO label_overrides (sighting_id, label, make, model, created_at) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(sighting_id) DO UPDATE SET label = excluded.label, "
                        "make = excluded.make, model = excluded.model, created_at = excluded.created_at",
                        (sighting_id, label, make, model, now_iso()))
        else:
            self._write("DELETE FROM label_overrides WHERE sighting_id = ?", (sighting_id,))

    def stars(self, kind: str) -> dict[str, str]:
        """{key: starred_at} for one kind."""
        return {r[0]: r[1] for r in self._read("SELECT key, created_at FROM stars WHERE kind = ?", (kind,))}

    # ------------------------------------------------------------ devices

    def add_device(self, device_id: str, name: str, platform: str, token_sha256: str) -> dict:
        self._write("INSERT INTO devices (id, name, platform, token_sha256, created_at, prefs) VALUES (?, ?, ?, ?, ?, ?)",
                    (device_id, name, platform, token_sha256, now_iso(), json.dumps(DEFAULT_PREFS)))
        return self.device(device_id)

    def device(self, device_id: str) -> dict | None:
        rows = self._read(f"SELECT {', '.join(DEVICE_COLS)} FROM devices WHERE id = ?", (device_id,))
        return self._dev(rows[0]) if rows else None

    def device_by_token(self, token_sha256: str) -> dict | None:
        rows = self._read(f"SELECT {', '.join(DEVICE_COLS)} FROM devices WHERE token_sha256 = ? AND revoked_at IS NULL",
                          (token_sha256,))
        return self._dev(rows[0]) if rows else None

    def devices(self, include_revoked: bool = False) -> list[dict]:
        where = "" if include_revoked else " WHERE revoked_at IS NULL"
        return [self._dev(r) for r in self._read(f"SELECT {', '.join(DEVICE_COLS)} FROM devices{where} "
                                                 "ORDER BY created_at")]

    def device_by_name(self, name: str, platform: str) -> dict | None:
        rows = self._read(f"SELECT {', '.join(DEVICE_COLS)} FROM devices WHERE name = ? AND platform = ? "
                          "AND revoked_at IS NULL ORDER BY created_at LIMIT 1", (name, platform))
        return self._dev(rows[0]) if rows else None

    @staticmethod
    def _dev(r) -> dict:
        d = dict(r)
        try:
            d["prefs"] = {**DEFAULT_PREFS, **json.loads(d["prefs"] or "{}")}
        except ValueError:
            d["prefs"] = dict(DEFAULT_PREFS)
        return d

    def set_token(self, device_id: str, token_sha256: str) -> None:
        self._write("UPDATE devices SET token_sha256 = ? WHERE id = ?", (token_sha256, device_id))

    def revoke(self, device_id: str) -> bool:
        return self._write("UPDATE devices SET revoked_at = ?, apns_token = NULL WHERE id = ? AND revoked_at IS NULL",
                           (now_iso(), device_id)) == 1

    def touch(self, device_id: str) -> None:
        t = time.time()
        if t - self._seen.get(device_id, 0) < 60:
            return
        self._seen[device_id] = t
        self._write("UPDATE devices SET last_seen_at = ? WHERE id = ?", (utc_iso(t), device_id))

    def set_push(self, device_id: str, apns_token: str | None, env: str, prefs: dict) -> None:
        self._write("UPDATE devices SET apns_token = ?, apns_env = ?, prefs = ? WHERE id = ?",
                    (apns_token, env, json.dumps(prefs), device_id))

    def clear_apns_token(self, device_id: str, apns_token: str) -> None:
        self._write("UPDATE devices SET apns_token = NULL WHERE id = ? AND apns_token = ?", (device_id, apns_token))

    def is_revoked_or_missing(self, device_id: str) -> bool:
        rows = self._read("SELECT revoked_at FROM devices WHERE id = ?", (device_id,))
        return not rows or rows[0][0] is not None

    # ------------------------------------------------------------ pairing codes

    def add_pair_code(self, code: str, ttl_s: float) -> None:
        now = time.time()
        self._write("DELETE FROM pair_codes WHERE expires_at < ?", (now - 86400,))
        self._write("INSERT INTO pair_codes (code, created_at, expires_at) VALUES (?, ?, ?)", (code, now_iso(), now + ttl_s))

    def use_pair_code(self, code: str) -> bool:
        """Atomically consume a valid code (single use)."""
        return self._write("UPDATE pair_codes SET used_at = ? WHERE code = ? AND used_at IS NULL AND expires_at > ?",
                           (now_iso(), code, time.time())) == 1

    # ------------------------------------------------------------ kv

    def get(self, key: str, default=None):
        rows = self._read("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def put(self, key: str, value) -> None:
        self._write("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)))

    def server_secret(self) -> bytes:
        """32 random bytes, created once (signs /media and /live URLs)."""
        v = self.get("server_secret")
        if v is None:
            self._write("INSERT OR IGNORE INTO kv (key, value) VALUES ('server_secret', ?)",
                        (json.dumps(secrets.token_hex(32)),))
            v = self.get("server_secret")
        return bytes.fromhex(v)

    # ------------------------------------------------------------ pushes

    def pushed(self, key: str, typ: str) -> bool:
        return bool(self._read("SELECT 1 FROM pushes WHERE sighting_id = ? AND type = ?", (key, typ)))

    def mark_pushed(self, key: str, typ: str) -> bool:
        """True if this (key, type) wasn't pushed before (and records it)."""
        return self._write("INSERT OR IGNORE INTO pushes (sighting_id, type, sent_at) VALUES (?, ?, ?)",
                           (key, typ, now_iso())) == 1
