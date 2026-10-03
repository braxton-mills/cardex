"""Sightings for the UI, read through sightings_db.connect_ro only, with the user's ui.db corrections applied.

Schema-tolerant: columns and tables are looked up per connection, so a v1 database (no year_range/color/source, no
discovered_labels) or a missing database reads as empty values instead of failing. ui.db is ATTACHed read-only to the
same connection, and every query goes through the `eff` CTE (sighting + label override + hidden + star), so a label
correction counts everywhere: lists, today's counts, highlights and the collection.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from urllib.parse import quote

from .. import sightings_db
from ..labels import Labels, labels_path
from ..sightings_db import local_day_bounds_utc, utc_iso
from .library import local_iso, media_url

RAW_COLS = ("kind", "ended_at", "yolo_class", "label", "make", "model", "confidence", "runner_ups", "unsure",
            "stationary", "direction", "track_frames", "max_box_px", "crop_path", "frame_path", "clip_path",
            "year_range", "color", "source", "siglip_label", "siglip_confidence")
SUMMARY = ("id", "started_at", "ended_at", "kind", "yolo_class", "label", "make", "model", "label_by",
           "original_label", "confidence", "unsure", "stationary", "direction", "year_range", "color", "starred",
           "hidden")
TIERS = ((0, "uncaught"), (1, "legendary"), (3, "rare"), (15, "uncommon"))  # count <= n -> tier; above: common
RARE_MAX = 3
BUSY_PER_DAY, BUSY_MIN = 3, 3


def tier(n: int) -> str:
    return next((name for limit, name in TIERS if n <= limit), "common")


def to_ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


class Sightings:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.root = cfg.paths.sightings
        self._cache: dict[str, tuple] = {}
        self._labels: tuple | None = None

    # ------------------------------------------------------------ plumbing

    def labels(self) -> Labels | None:
        """sightings_labels.txt, re-read when it changes; None if it is missing or empty."""
        p = labels_path(self.cfg)
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            return None
        if not self._labels or self._labels[0] != mtime:
            try:
                self._labels = (mtime, Labels(p))
            except (OSError, ValueError):
                self._labels = (mtime, None)
        return self._labels[1]

    def exists(self) -> bool:
        return sightings_db.db_path(self.root).exists()

    @contextmanager
    def _open(self):
        """(connection, eff CTE sql) or (None, None) when there is no sightings table yet."""
        con = sightings_db.connect_ro(self.root)
        if con is None:
            yield None, None
            return
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
            if "sightings" not in tables:
                yield None, None
                return
            cols = {r[1] for r in con.execute("PRAGMA main.table_info(sightings)")}
            con.execute("ATTACH DATABASE ? AS ui", (f"file:{quote(self.store.path.as_posix(), safe='/:')}?mode=ro",))
            yield con, self._eff(cols)
        finally:
            con.close()

    @staticmethod
    def _has_table(con, name: str) -> bool:
        return con.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?",
                           (name,)).fetchone() is not None

    @staticmethod
    def _eff(cols: set) -> str:
        def c(name):
            return f"s.{name}" if name in cols else "NULL"
        raw = ",\n  ".join(f"{c(n)} AS raw_{n}" for n in RAW_COLS)
        return f"""eff AS (
SELECT s.id AS id, s.started_at AS started_at,
  {raw},
  {c('kind')} AS kind, {c('ended_at')} AS ended_at, {c('yolo_class')} AS yolo_class,
  {c('confidence')} AS confidence, {c('direction')} AS direction, {c('year_range')} AS year_range,
  {c('color')} AS color,
  CASE WHEN o.sighting_id IS NOT NULL THEN o.label ELSE {c('label')} END AS label,
  CASE WHEN o.sighting_id IS NOT NULL THEN o.make ELSE {c('make')} END AS make,
  CASE WHEN o.sighting_id IS NOT NULL THEN o.model ELSE {c('model')} END AS model,
  CASE WHEN o.sighting_id IS NOT NULL THEN 'user' WHEN {c('source')} = 'cloud' THEN 'cloud' ELSE 'siglip' END
    AS label_by,
  {c('label')} AS original_label,
  CASE WHEN o.sighting_id IS NOT NULL THEN 0 ELSE COALESCE({c('unsure')}, 0) END AS unsure,
  COALESCE({c('stationary')}, 0) AS stationary,
  st.created_at IS NOT NULL AS starred,
  h.sighting_id IS NOT NULL AS hidden
FROM main.sightings s
LEFT JOIN ui.label_overrides o ON o.sighting_id = s.id
LEFT JOIN ui.hidden h ON h.sighting_id = s.id
LEFT JOIN ui.stars st ON st.kind = 'sighting' AND st.key = s.id)"""

    def _signature(self) -> tuple:
        """Changes whenever the service commits (WAL or main file) or the user writes ui.db."""
        sig = [self.store.generation]
        for suffix in ("", "-wal"):
            try:
                s = (self.root / f"sightings.db{suffix}").stat()
                sig += [s.st_mtime_ns, s.st_size]
            except OSError:
                sig += [None, None]
        return tuple(sig)

    def _cached(self, name: str, fn):
        sig = self._signature()
        hit = self._cache.get(name)
        if hit and hit[0] == sig:
            return hit[1]
        val = fn()
        self._cache[name] = (sig, val)
        return val

    # ------------------------------------------------------------ rows

    def _summary(self, r) -> dict:
        d = {k: r[k] for k in SUMMARY}
        d["unsure"], d["stationary"] = bool(d["unsure"]), bool(d["stationary"])
        d["starred"], d["hidden"] = bool(d["starred"]), bool(d["hidden"])
        d["started_local"] = local_iso(to_ts(r["started_at"]))
        d["ended_local"] = local_iso(to_ts(r["ended_at"])) if r["ended_at"] else None
        d["crop_url"] = media_url("sightings", r["raw_crop_path"]) if r["raw_crop_path"] else None
        clip = r["raw_clip_path"]
        d["has_clip"] = bool(clip) and (self.root / clip).is_file()
        return d

    def _detail(self, con, r) -> dict:
        d = self._summary(r)
        try:
            d["runner_ups"] = json.loads(r["raw_runner_ups"] or "[]")
        except ValueError:
            d["runner_ups"] = []
        d["frame_url"] = media_url("sightings", r["raw_frame_path"]) if r["raw_frame_path"] else None
        d["clip_url"] = media_url("sightings", r["raw_clip_path"]) if d["has_clip"] else None
        d["source"] = r["raw_source"] or "siglip"
        d["siglip_label"], d["siglip_confidence"] = r["raw_siglip_label"], r["raw_siglip_confidence"]
        d["model_label"], d["model_make"], d["model_model"] = r["raw_label"], r["raw_make"], r["raw_model"]
        d["track_frames"], d["max_box_px"] = r["raw_track_frames"], r["raw_max_box_px"]
        d["cloud"] = None
        if self._has_table(con, "cloud_queue"):
            q = con.execute("SELECT done_at, last_error, attempts FROM cloud_queue WHERE sighting_id = ?",
                            (r["id"],)).fetchone()
            if q:
                d["cloud"] = {"state": "pending" if not q["done_at"] else ("error" if q["last_error"] else "done"),
                              "error": q["last_error"], "attempts": q["attempts"], "done_at": q["done_at"]}
        return d

    def list(self, f: dict, cursor: str | None = None, limit: int = 60) -> dict:
        where, params = ["1"], []
        if not f.get("include_hidden"):
            where.append("NOT hidden")
        if f.get("from"):
            where.append("started_at >= ?")
            params.append(local_day_bounds_utc(date.fromisoformat(f["from"]))[0])
        if f.get("to"):
            where.append("started_at < ?")
            params.append(local_day_bounds_utc(date.fromisoformat(f["to"]))[1])
        for key, col in (("make", "make"), ("class", "yolo_class"), ("source", "label_by"), ("label", "label")):
            if f.get(key):
                where.append(f"{col} = ? COLLATE NOCASE")
                params.append(f[key])
        if f.get("hide_unsure"):
            where.append("NOT unsure")
        if f.get("hide_stationary"):
            where.append("NOT stationary")
        if f.get("starred"):
            where.append("starred")
        if cursor:
            at, _, sid = cursor.partition("|")
            where.append("(started_at < ? OR (started_at = ? AND id < ?))")
            params += [at, at, sid]
        limit = max(1, min(int(limit), 500))
        with self._open() as (con, eff):
            if con is None:
                return {"items": [], "next_cursor": None}
            rows = con.execute(f"WITH {eff} SELECT * FROM eff WHERE {' AND '.join(where)} "
                               "ORDER BY started_at DESC, id DESC LIMIT ?", (*params, limit + 1)).fetchall()
            items = [self._summary(r) for r in rows[:limit]]
        nxt = f"{rows[limit - 1]['started_at']}|{rows[limit - 1]['id']}" if len(rows) > limit else None
        return {"items": items, "next_cursor": nxt}

    def get(self, sid: str) -> dict | None:
        with self._open() as (con, eff):
            if con is None:
                return None
            r = con.execute(f"WITH {eff} SELECT * FROM eff WHERE id = ?", (sid,)).fetchone()
            return self._detail(con, r) if r else None

    def known(self, sid: str) -> bool:
        with self._open() as (con, _):
            return bool(con and con.execute("SELECT 1 FROM main.sightings WHERE id = ?", (sid,)).fetchone())

    def starts_between(self, lo: float, hi: float) -> list[float]:
        """Sorted start times (epoch) of visible sightings in [lo, hi), for per-clip counts."""
        with self._open() as (con, eff):
            if con is None:
                return []
            rows = con.execute(f"WITH {eff} SELECT started_at FROM eff WHERE NOT hidden AND started_at >= ? "
                               "AND started_at < ?", (utc_iso(lo), utc_iso(hi))).fetchall()
        return sorted(to_ts(r[0]) for r in rows)

    # ------------------------------------------------------------ aggregates (cached)

    def label_stats(self) -> dict[str, dict]:
        """{label lower: {label, make, model, count, first, last, label_by set}} over visible sightings."""
        def build():
            with self._open() as (con, eff):
                if con is None:
                    return {}
                rows = con.execute(f"WITH {eff} SELECT label, make, model, COUNT(*) AS n, MIN(started_at) AS first, "
                                   "MAX(started_at) AS last FROM eff WHERE NOT hidden AND label IS NOT NULL "
                                   "GROUP BY label COLLATE NOCASE").fetchall()
                disc = (con.execute("SELECT label, make, model, first_seen FROM main.discovered_labels").fetchall()
                        if self._has_table(con, "discovered_labels") else [])
            out = {r["label"].lower(): {"label": r["label"], "make": r["make"], "model": r["model"],
                                        "count": r["n"], "first": r["first"], "last": r["last"]} for r in rows}
            for r in disc:
                out.setdefault(r["label"].lower(), {"label": r["label"], "make": r["make"], "model": r["model"],
                                                    "count": 0, "first": None, "last": None})["discovered_table"] = True
            return out
        return self._cached("label_stats", build)

    def collection(self) -> dict:
        stats = self.label_stats()
        labels = self.labels()
        items, listed = [], set()
        if labels:
            for name, mk, md in zip(labels.names, labels.make, labels.model):
                s = stats.get(name.lower(), {})
                listed.add(name.lower())
                items.append(self._tile(name, mk, md, s, generic=mk is None, discovered=False))
        for key, s in sorted(stats.items(), key=lambda kv: kv[1]["first"] or "9"):
            if key not in listed and s["count"] > 0:  # named by Gemini (or by you) but not in sightings_labels.txt
                items.append(self._tile(s["label"], s["make"], s["model"], s, generic=False, discovered=True))
        listed_items = [i for i in items if not i["discovered"]]
        return {"caught": sum(1 for i in listed_items if i["count"]), "total": len(listed_items),
                "discovered": sum(1 for i in items if i["discovered"]), "items": items}

    @staticmethod
    def _tile(label, make, model, s: dict, generic: bool, discovered: bool) -> dict:
        n = s.get("count", 0)
        first, last = s.get("first"), s.get("last")
        return {"label": label, "make": make, "model": model, "generic": generic, "discovered": discovered,
                "count": n, "tier": tier(n), "first_seen": first, "last_seen": last,
                "first_seen_local": local_iso(to_ts(first)) if first else None,
                "last_seen_local": local_iso(to_ts(last)) if last else None}

    def today(self) -> dict:
        def build():
            lo, hi = local_day_bounds_utc()
            out = {"total": 0, "by_class": {}, "by_label_by": {}, "labels": 0, "new_catches": 0, "starred": 0}
            with self._open() as (con, eff):
                if con is None:
                    return out
                for r in con.execute(f"WITH {eff} SELECT yolo_class, label_by, COUNT(*) AS n, SUM(starred) AS s "
                                     "FROM eff WHERE NOT hidden AND started_at >= ? AND started_at < ? "
                                     "GROUP BY yolo_class, label_by", (lo, hi)):
                    out["total"] += r["n"]
                    out["starred"] += r["s"] or 0
                    out["by_class"][r["yolo_class"] or "?"] = out["by_class"].get(r["yolo_class"] or "?", 0) + r["n"]
                    out["by_label_by"][r["label_by"]] = out["by_label_by"].get(r["label_by"], 0) + r["n"]
                out["labels"] = con.execute(f"WITH {eff} SELECT COUNT(DISTINCT label COLLATE NOCASE) FROM eff "
                                            "WHERE NOT hidden AND started_at >= ? AND started_at < ?",
                                            (lo, hi)).fetchone()[0]
            out["new_catches"] = sum(1 for s in self.label_stats().values() if s["first"] and lo <= s["first"] < hi)
            return out
        return self._cached("today", build)

    def highlight_items(self) -> list[dict]:
        """Sighting-based highlights: new catches, rare labels, starred sightings (one item per sighting, with every
        type that applies) and the busiest windows per day. Unsorted; app.py merges in videos and sorts."""
        def build():
            step = int(self.cfg.render.interval_min) * 60
            with self._open() as (con, eff):
                if con is None:
                    return []
                rows = con.execute(f"""WITH {eff}, ranked AS (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY label COLLATE NOCASE ORDER BY started_at, id) AS nth,
                              COUNT(*) OVER (PARTITION BY label COLLATE NOCASE) AS label_count
                    FROM eff WHERE NOT hidden AND label IS NOT NULL)
                    SELECT * FROM ranked WHERE nth = 1 OR label_count <= ? OR starred""", (RARE_MAX,)).fetchall()
                windows = con.execute(f"WITH {eff} SELECT CAST(strftime('%s', started_at) AS INTEGER) / ? AS w, "
                                      "COUNT(*) AS n FROM eff WHERE NOT hidden AND NOT stationary GROUP BY w",
                                      (step,)).fetchall()
            items = []
            for r in rows:
                types = [t for t, ok in (("new_catch", r["nth"] == 1), ("rare", r["label_count"] <= RARE_MAX),
                                         ("starred", r["starred"])) if ok]
                s = self._summary(r)
                s["label_count"] = r["label_count"]
                title = {"new_catch": f"New catch: {r['label']}", "rare": f"Rare: {r['label']}",
                         "starred": f"Starred: {r['label']}"}[types[0]]
                items.append({"type": types[0], "types": types, "at": r["started_at"], "title": title,
                              "sighting": s})
            by_day: dict[date, list] = {}
            for r in windows:
                start = r["w"] * step
                by_day.setdefault(datetime.fromtimestamp(start).date(), []).append((r["n"], start))
            for day, ws in by_day.items():
                for n, start in sorted(ws, reverse=True)[:BUSY_PER_DAY]:
                    if n < BUSY_MIN:
                        break
                    items.append({"type": "busy_window", "types": ["busy_window"], "at": utc_iso(start + step),
                                  "title": f"Busy: {n} sightings {datetime.fromtimestamp(start):%H:%M}"
                                           f"-{datetime.fromtimestamp(start + step):%H:%M}",
                                  "window": {"start_at": utc_iso(start), "start_local": local_iso(start),
                                             "end_at": utc_iso(start + step), "end_local": local_iso(start + step),
                                             "sightings": n, "day": day.isoformat()}})
            return items
        return self._cached("highlights", build)

    def resolve_label(self, label: str) -> tuple[str, str | None, str | None]:
        """Canonical spelling + make/model for a user-typed label (from the label list or a discovered label)."""
        labels = self.labels()
        if labels:
            for name, mk, md in zip(labels.names, labels.make, labels.model):
                if name.lower() == label.lower():
                    return name, mk, md
        s = self.label_stats().get(label.lower())
        if s:
            return s["label"], s["make"], s["model"]
        return label, None, None
