"""Sightings in the contract's shapes (§5.2, §6.1, §6.2), read through sightings_db.connect_ro only, with the user's
ui.db state applied.

Schema-tolerant: columns and tables are looked up per connection, so a v1 database (no source/siglip_*/year_range/
color, no discovered_labels) or a v2 one (no cloud_status) reads as nulls, and a missing database as empty lists.
ui.db is ATTACHed read-only to the same connection and every query goes through the `eff` CTE (sighting + label
correction + hidden + star), so a correction counts everywhere: lists, counts, highlights and the collection.

Counting (§6.1): a sighting counts when it isn't hidden, has an effective label, and isn't unsure unless Gemini or
the user decided it. Labels are grouped case-insensitively and reported in the collection's spelling.
"""
from __future__ import annotations

import bisect
import json
import threading
import zlib
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
from urllib.parse import quote

from .. import sightings_db
from ..labels import Labels, labels_path
from ..sightings_db import local_day_bounds_utc
from .contract import (ApiError, bool_param, date_param, decode_cursor, encode_cursor, iso, iso_from_utc,
                       limit_param, local_day, multi, not_found, one, utc_to_ts)

RAW_COLS = ("kind", "ended_at", "yolo_class", "label", "make", "model", "confidence", "runner_ups", "unsure",
            "stationary", "direction", "track_frames", "max_box_px", "crop_path", "frame_path", "clip_path",
            "year_range", "color", "source", "siglip_label", "siglip_confidence", "cloud_status")
TIERS = [{"tier": "rare", "min": 1, "max": 3}, {"tier": "uncommon", "min": 4, "max": 20},
         {"tier": "common", "min": 21, "max": None}]
DECIDERS = ("siglip", "cloud", "user")
CLOUD_STATUSES = ("pending", "done", "failed", "capped", "skipped")


def tier(n: int) -> str:
    if n <= 0:
        return "uncaught"
    return next(t["tier"] for t in TIERS if n >= t["min"] and (t["max"] is None or n <= t["max"]))


# Card finishes (desktop Cards tab): percentile of count among labels seen at least twice, rarest first. Seen exactly
# once is always "sir". Returned in /api/cards so the UI never hardcodes the cut-offs.
FINISHES = [
    {"finish": "sir", "label": "Special Illustration Rare", "symbol": "gold-star", "min_pct": None, "max_pct": None},
    {"finish": "fullart", "label": "Full Art", "symbol": "double-star", "min_pct": 0.0, "max_pct": 0.10},
    {"finish": "holo", "label": "Holo Rare", "symbol": "star", "min_pct": 0.10, "max_pct": 0.25},
    {"finish": "reverse", "label": "Reverse Holo", "symbol": "diamond", "min_pct": 0.25, "max_pct": 0.50},
    {"finish": "plain", "label": "Common", "symbol": "circle", "min_pct": 0.50, "max_pct": None},
]
FINISHES_BY_PCT = [f for f in FINISHES if f["min_pct"] is not None]

# Gemini's free-text colours -> paint. Unknown labels get a deterministic everyday colour (known: false).
PAINT = {"black": "#0b0c10", "white": "#e9ecef", "silver": "#aeb4bc", "gray": "#5d636b", "grey": "#5d636b",
         "dark gray": "#33373d", "dark grey": "#33373d", "blue": "#1f4fa8", "dark blue": "#152a55", "red": "#a3121c",
         "maroon": "#5a1020", "yellow": "#e3b505", "green": "#1d5a35", "dark green": "#163a26", "brown": "#4f3524",
         "tan": "#b39a73", "orange": "#d4561a", "gold": "#b8933e", "beige": "#cbb994", "purple": "#4b2a6b",
         "dark": "#22252a"}
EVERYDAY = ["white", "black", "silver", "gray", "blue", "red", "dark blue", "dark gray"]


def card_color(label: str, name: str | None) -> dict:
    if name:
        hexv = PAINT.get(name) or next((v for k, v in PAINT.items() if k in name), None)
        if hexv:
            return {"name": name, "hex": hexv, "known": True}
    pick = EVERYDAY[zlib.crc32(label.lower().encode()) % len(EVERYDAY)]
    return {"name": pick, "hex": PAINT[pick], "known": False}


class Sightings:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.root = cfg.paths.sightings
        self._cache: dict[str, tuple] = {}
        self._cache_lock = threading.Lock()
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
            if not self._has_table(con, "sightings"):
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
SELECT s.rowid AS rowid, s.id AS id, s.started_at AS started_at, {c('ended_at')} AS ended_at,
  {raw},
  {c('yolo_class')} AS yolo_class,
  CASE WHEN o.sighting_id IS NOT NULL THEN o.label ELSE {c('label')} END AS label,
  CASE WHEN o.sighting_id IS NOT NULL THEN o.make ELSE {c('make')} END AS make,
  CASE WHEN o.sighting_id IS NOT NULL THEN o.model ELSE {c('model')} END AS model,
  CASE WHEN o.sighting_id IS NOT NULL THEN 'user' WHEN {c('source')} = 'cloud' THEN 'cloud' ELSE 'siglip' END
    AS decided_by,
  COALESCE({c('unsure')}, 0) AS unsure,
  COALESCE({c('stationary')}, 0) AS stationary,
  o.label AS corr_label, o.created_at AS corr_at,
  st.created_at IS NOT NULL AS starred,
  h.sighting_id IS NOT NULL AS hidden
FROM main.sightings s
LEFT JOIN ui.label_overrides o ON o.sighting_id = s.id
LEFT JOIN ui.hidden h ON h.sighting_id = s.id
LEFT JOIN ui.stars st ON st.kind = 'sighting' AND st.key = s.id),
counted AS (SELECT * FROM eff WHERE NOT hidden AND label IS NOT NULL AND label != ''
            AND (NOT unsure OR decided_by IN ('cloud', 'user')))"""

    def signature(self) -> tuple:
        """Changes whenever the service commits (WAL or main file) or anyone writes ui.db."""
        sig = list(self.store.signature())
        for suffix in ("", "-wal"):
            try:
                s = (self.root / f"sightings.db{suffix}").stat()
                sig += [s.st_mtime_ns, s.st_size]
            except OSError:
                sig += [None, None]
        lp = labels_path(self.cfg)
        try:
            sig.append(lp.stat().st_mtime_ns)
        except OSError:
            sig.append(None)
        return tuple(sig)

    def _cached(self, name: str, fn):
        sig = self.signature()
        with self._cache_lock:
            hit = self._cache.get(name)
            if hit and hit[0] == sig:
                return hit[1]
        val = fn()
        with self._cache_lock:
            self._cache[name] = (sig, val)
        return val

    # ------------------------------------------------------------ labels / collection index

    def catalog(self) -> dict:
        """The collection's label universe: {lower: {label, make, model, generic, origin}} in collection order
        (labels file, then discovered), plus the counted rows grouped by label. Cached."""
        def build():
            labels = self.labels()
            known: dict[str, dict] = {}
            if labels:
                for name, mk, md in zip(labels.names, labels.make, labels.model):
                    known.setdefault(name.lower(), {"label": name, "make": mk, "model": md, "generic": mk is None,
                                                    "origin": "labels_file"})
            rows, disc = [], []
            with self._open() as (con, eff):
                if con is not None:
                    rows = con.execute(f"WITH {eff} SELECT rowid, id, started_at, label, make, model, raw_confidence, "
                                       "raw_crop_path FROM counted ORDER BY started_at, id").fetchall()
                    if self._has_table(con, "discovered_labels"):
                        disc = con.execute("SELECT label, make, model, first_seen FROM main.discovered_labels "
                                           "ORDER BY first_seen").fetchall()
            for r in disc:
                if r["label"] and r["label"].lower() not in known:
                    known[r["label"].lower()] = {"label": r["label"], "make": r["make"], "model": r["model"],
                                                 "generic": False, "origin": "discovered",
                                                 "first_seen": r["first_seen"]}
            by: dict[str, list] = {}
            for r in rows:
                key = r["label"].lower()
                if key not in known:
                    known[key] = {"label": r["label"], "make": r["make"], "model": r["model"], "generic": False,
                                  "origin": "other"}
                by.setdefault(key, []).append({"rowid": r["rowid"], "id": r["id"], "ts": utc_to_ts(r["started_at"]),
                                               "started_at": r["started_at"], "confidence": r["raw_confidence"],
                                               "crop": r["raw_crop_path"]})
            starts = sorted(x["ts"] for lst in by.values() for x in lst)
            return {"known": known, "by": by, "starts": starts}
        return self._cached("catalog", build)

    def canon(self, label: str | None) -> str | None:
        if not label:
            return label
        k = self.catalog()["known"].get(label.lower())
        return k["label"] if k else label

    def count_between(self, lo: float, hi: float) -> int | None:
        """Counted sightings with started_at in [lo, hi); None without a sightings database."""
        if not self.exists():
            return None
        s = self.catalog()["starts"]
        return bisect.bisect_left(s, hi) - bisect.bisect_left(s, lo)

    def collection(self, signer) -> dict:
        cat = self.catalog()
        known, by = cat["known"], cat["by"]
        items = []
        for key, k in known.items():
            rows = by.get(key, [])
            if k["origin"] == "other" and not rows:
                continue
            best = max((s for s in rows if s["crop"]), key=lambda s: (s["confidence"] or 0, s["ts"], s["id"]),
                       default=None)
            items.append({
                "label": k["label"], "make": k["make"], "model": k["model"], "generic": k["generic"],
                "origin": k["origin"], "count": len(rows),
                "first_seen_at": iso(rows[0]["ts"]) if rows else None,
                "last_seen_at": iso(rows[-1]["ts"]) if rows else None,
                "tier": tier(len(rows)),
                "cover": ({"sighting_id": best["id"], "crop": signer(self.media_path(best["crop"]))}
                          if best and best["crop"] else None),
                "_sort": (rows[0]["ts"] if rows else utc_to_ts(k.get("first_seen")) or float("inf")),
            })
        order = {"labels_file": 0, "discovered": 1, "other": 2}
        lf = [i for i in items if i["origin"] == "labels_file"]
        dc = sorted((i for i in items if i["origin"] == "discovered"), key=lambda i: i["_sort"])
        ot = sorted((i for i in items if i["origin"] == "other"), key=lambda i: i["label"].lower())
        items = sorted(lf + dc + ot, key=lambda i: order[i["origin"]])
        for i in items:
            i.pop("_sort")
        return {"total": len(items), "caught": sum(1 for i in items if i["count"] > 0), "tiers": TIERS,
                "items": items}

    # ------------------------------------------------------------ cards (desktop only, not in the contract)

    def card_stats(self) -> dict:
        """Per counted label: yolo class, Gemini colour, year range, direction and hour tallies, best crop and frame
        (confidence x box size). Cached like the catalog."""
        def build():
            out: dict[str, dict] = {}
            with self._open() as (con, eff):
                if con is None:
                    return out
                rows = con.execute(f"WITH {eff} SELECT id, started_at, label, yolo_class, raw_color, raw_year_range, "
                                   "raw_direction, raw_crop_path, raw_frame_path, raw_max_box_px, raw_confidence "
                                   "FROM counted").fetchall()
            for r in rows:
                s = out.setdefault(r["label"].lower(), {"cls": Counter(), "color": Counter(), "years": Counter(),
                                                        "dir": Counter(), "hour": Counter(), "best": None,
                                                        "best_score": -1.0, "best_conf": 0.0})
                s["cls"][r["yolo_class"]] += 1
                if r["raw_color"]:
                    s["color"][r["raw_color"].strip().lower()] += 1
                if r["raw_year_range"]:
                    s["years"][r["raw_year_range"]] += 1
                if r["raw_direction"] in ("LR", "RL"):
                    s["dir"][r["raw_direction"]] += 1
                ts = utc_to_ts(r["started_at"])
                if ts is not None:
                    s["hour"][datetime.fromtimestamp(ts).hour] += 1
                conf = r["raw_confidence"] or 0.0
                s["best_conf"] = max(s["best_conf"], conf)
                score = conf * (r["raw_max_box_px"] or 0)
                if r["raw_crop_path"] and score > s["best_score"]:
                    s["best_score"], s["best"] = score, {"id": r["id"], "crop": r["raw_crop_path"],
                                                         "frame": r["raw_frame_path"],
                                                         "box_px": r["raw_max_box_px"],
                                                         "direction": r["raw_direction"]}
            return out
        return self._cached("card_stats", build)

    def mesh_candidates(self) -> dict:
        """Per counted label: every sighting with a crop, for card_meshes to choose its source photo from."""
        def build():
            out: dict[str, list] = {}
            with self._open() as (con, eff):
                if con is None:
                    return out
                rows = con.execute(f"WITH {eff} SELECT id, label, raw_direction, raw_crop_path, raw_frame_path, "
                                   "raw_max_box_px, raw_color FROM counted WHERE raw_crop_path IS NOT NULL").fetchall()
            for r in rows:
                out.setdefault(r["label"].lower(), []).append(
                    {"id": r["id"], "crop": r["raw_crop_path"], "frame": r["raw_frame_path"],
                     "box_px": r["raw_max_box_px"] or 0, "direction": r["raw_direction"],
                     "color": (r["raw_color"] or "").strip().lower() or None})
            return out
        return self._cached("mesh_candidates", build)

    def mesh_manifest(self) -> dict:
        """card_meshes' manifest.json ({label lower: {file, sighting_id, box_px, generated_at}}), re-read on change."""
        p = self.cfg.paths.card_meshes / "manifest.json"
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            return {}
        hit = self._cache.get("mesh_manifest")
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            val = json.loads(p.read_text(encoding="utf-8"))
            val = val if isinstance(val, dict) else {}
        except (OSError, ValueError):
            val = {}
        self._cache["mesh_manifest"] = (mtime, val)
        return val

    def cards(self, signer) -> dict:
        """GET /api/cards: the collection as trading cards. Finishes are percentiles of count among labels seen at
        least twice (rarest first); a label seen exactly once is a Special Illustration Rare."""
        col = self.collection(signer)
        stats, meshes = self.card_stats(), self.mesh_manifest()
        caught = sorted(i["count"] for i in col["items"] if i["count"] >= 2)
        items = []
        for n, i in enumerate(col["items"], 1):
            key = i["label"].lower()
            s = stats.get(key)
            c = i["count"]
            pct = (bisect.bisect_left(caught, c) / len(caught)) if c >= 2 and caught else None
            finish = None if c <= 0 else "sir" if c == 1 else next(f["finish"] for f in FINISHES_BY_PCT
                                                                    if f["max_pct"] is None or pct < f["max_pct"])
            color_name = s["color"].most_common(1)[0][0] if s and s["color"] else None
            best = s["best"] if s else None
            m = meshes.get(key)
            items.append({
                "label": i["label"], "make": i["make"], "model": i["model"], "generic": i["generic"],
                "origin": i["origin"], "number": n, "count": c, "finish": finish,
                "pct": round(pct, 4) if pct is not None else None,
                "first_seen_at": i["first_seen_at"], "last_seen_at": i["last_seen_at"],
                "yolo_class": s["cls"].most_common(1)[0][0] if s and s["cls"] else None,
                "color": card_color(i["label"], color_name),
                "year_range": s["years"].most_common(1)[0][0] if s and s["years"] else None,
                "peak_hour": s["hour"].most_common(1)[0][0] if s and s["hour"] else None,
                "directions": {"LR": s["dir"]["LR"], "RL": s["dir"]["RL"]} if s else {"LR": 0, "RL": 0},
                "best_confidence": round(s["best_conf"], 3) if s else None,
                "cover": ({"sighting_id": best["id"], "crop": signer(self.media_path(best["crop"])),
                           "frame": signer(self.media_path(best["frame"])) if best["frame"] else None}
                          if best else None),
                "mesh": ({"url": signer(f"/media/cards/{m['file']}"), "generated_at": m.get("generated_at"),
                          "source_sighting": m.get("sighting_id")}
                         if m and (self.cfg.paths.card_meshes / m["file"]).is_file() else None),
            })
        return {"generated_at": iso(datetime.now().timestamp()), "set_total": len(items),
                "caught": sum(1 for i in items if i["count"] > 0), "finishes": FINISHES, "items": items}

    def resolve_label(self, label: str) -> dict:
        """The collection item a correction names (§4.5); 422 unknown_label otherwise."""
        k = self.catalog()["known"].get(label.strip().lower())
        if not k:
            raise ApiError(422, "unknown_label", f"{label!r} is not in the collection")
        return k

    # ------------------------------------------------------------ rows

    @staticmethod
    def media_path(rel: str) -> str:
        return f"/media/sightings/{quote(rel)}"

    def view(self, r, signer) -> dict:
        """One sightings row (from eff) as a contract Sighting."""
        source = "cloud" if r["raw_source"] == "cloud" else "siglip"
        try:
            ups = [{"label": str(u["label"]), "p": float(u["p"])} for u in json.loads(r["raw_runner_ups"] or "[]")
                   if isinstance(u, dict) and u.get("label") is not None and u.get("p") is not None]
        except (ValueError, TypeError):
            ups = []
        clip = r["raw_clip_path"]
        has_clip = bool(clip) and (self.root / clip).is_file()
        siglip_label = r["raw_siglip_label"] if r["raw_source"] is not None else r["raw_label"]
        siglip_conf = r["raw_siglip_confidence"] if r["raw_source"] is not None else r["raw_confidence"]
        started = utc_to_ts(r["started_at"])
        cloud_status = r["raw_cloud_status"] if r["raw_cloud_status"] in CLOUD_STATUSES else None
        return {
            "id": r["id"], "kind": r["raw_kind"] or "vehicle",
            "started_at": iso(started), "ended_at": iso_from_utc(r["ended_at"] or r["started_at"]),
            "day": local_day(started).isoformat(), "yolo_class": r["yolo_class"],
            "label": self.canon(r["label"]) or None, "make": r["make"], "model": r["model"],
            "confidence": r["raw_confidence"], "decided_by": r["decided_by"],
            "machine": {"label": r["raw_label"], "make": r["raw_make"], "model": r["raw_model"],
                        "confidence": r["raw_confidence"], "source": source},
            "siglip": {"label": siglip_label, "confidence": siglip_conf},
            "runner_ups": ups, "year_range": r["raw_year_range"], "color": r["raw_color"],
            "unsure": bool(r["unsure"]), "stationary": bool(r["stationary"]),
            "direction": r["raw_direction"] if r["raw_direction"] in ("LR", "RL") else None,
            "cloud_status": cloud_status, "starred": bool(r["starred"]), "hidden": bool(r["hidden"]),
            "correction": {"label": self.canon(r["corr_label"]), "at": iso_from_utc(r["corr_at"])}
            if r["corr_label"] else None,
            "track_frames": r["raw_track_frames"], "max_box_px": r["raw_max_box_px"],
            "media": {"crop": signer(self.media_path(r["raw_crop_path"])) if r["raw_crop_path"] else None,
                      "frame": signer(self.media_path(r["raw_frame_path"])) if r["raw_frame_path"] else None,
                      "clip": signer(self.media_path(clip)) if has_clip else None},
        }

    def list(self, q, signer) -> dict:
        """GET /api/sightings (§4.4): filters, newest first, keyset paging, total."""
        frm, to = date_param(q, "from"), date_param(q, "to")
        classes, makes = multi(q, "class"), multi(q, "make")
        label = one(q, "label")
        decided = multi(q, "decided_by", DECIDERS)
        starred, hide_unsure = bool_param(q, "starred"), bool_param(q, "hide_unsure")
        hide_stat, incl_hidden = bool_param(q, "hide_stationary"), bool_param(q, "include_hidden")
        limit = limit_param(q)
        cur = one(q, "cursor")
        after = decode_cursor(cur, 2) if cur else None
        where, params = ["1"], []
        if not incl_hidden:
            where.append("NOT hidden")
        if frm:
            where.append("started_at >= ?")
            params.append(local_day_bounds_utc(frm)[0])
        if to:
            where.append("started_at < ?")
            params.append(local_day_bounds_utc(to)[1])
        for vals, col in ((classes, "yolo_class"), (makes, "make"), (decided, "decided_by")):
            if vals:
                where.append(f"{col} COLLATE NOCASE IN ({', '.join('?' * len(vals))})")
                params += vals
        if label:
            where.append("label = ? COLLATE NOCASE")
            params.append(label)
        if starred:
            where.append("starred")
        if hide_unsure:
            where.append("NOT (unsure AND decided_by = 'siglip')")
        if hide_stat:
            where.append("NOT stationary")
        with self._open() as (con, eff):
            if con is None:
                return {"items": [], "next_cursor": None, "total": 0}
            sql_where = " AND ".join(where)
            total = con.execute(f"WITH {eff} SELECT COUNT(*) FROM eff WHERE {sql_where}", params).fetchone()[0]
            page_where, page_params = sql_where, list(params)
            if after:
                page_where += " AND (started_at < ? OR (started_at = ? AND id < ?))"
                page_params += [after[0], after[0], after[1]]
            rows = con.execute(f"WITH {eff} SELECT * FROM eff WHERE {page_where} "
                               "ORDER BY started_at DESC, id DESC LIMIT ?", (*page_params, limit + 1)).fetchall()
        items = [self.view(r, signer) for r in rows[:limit]]
        nxt = encode_cursor([rows[limit - 1]["started_at"], rows[limit - 1]["id"]]) if len(rows) > limit else None
        return {"items": items, "next_cursor": nxt, "total": total}

    def row(self, sid: str):
        with self._open() as (con, eff):
            if con is None:
                return None
            return con.execute(f"WITH {eff} SELECT * FROM eff WHERE id = ?", (sid,)).fetchone()

    def get(self, sid: str, signer) -> dict:
        r = self.row(sid)
        if r is None:
            raise not_found(f"no sighting {sid}")
        return self.view(r, signer)

    def rows_by_id(self, ids: list[str]) -> dict:
        if not ids:
            return {}
        with self._open() as (con, eff):
            if con is None:
                return {}
            out = {}
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for r in con.execute(f"WITH {eff} SELECT * FROM eff WHERE id IN ({', '.join('?' * len(chunk))})",
                                     chunk):
                    out[r["id"]] = r
            return out

    # ------------------------------------------------------------ desktop-only aggregates (additive endpoints)

    def today(self) -> dict:
        def build():
            lo, hi = local_day_bounds_utc()
            out = {"day": date.today().isoformat(), "total": 0, "counted": 0, "by_class": {}, "by_decided_by": {},
                   "labels": 0, "new_catches": 0, "starred": 0}
            with self._open() as (con, eff):
                if con is None:
                    return out
                for r in con.execute(f"WITH {eff} SELECT yolo_class, decided_by, COUNT(*) AS n, SUM(starred) AS s "
                                     "FROM eff WHERE NOT hidden AND started_at >= ? AND started_at < ? "
                                     "GROUP BY yolo_class, decided_by", (lo, hi)):
                    out["total"] += r["n"]
                    out["starred"] += r["s"] or 0
                    k = r["yolo_class"] or "?"
                    out["by_class"][k] = out["by_class"].get(k, 0) + r["n"]
                    out["by_decided_by"][r["decided_by"]] = out["by_decided_by"].get(r["decided_by"], 0) + r["n"]
                r = con.execute(f"WITH {eff} SELECT COUNT(*), COUNT(DISTINCT label COLLATE NOCASE) FROM counted "
                                "WHERE started_at >= ? AND started_at < ?", (lo, hi)).fetchone()
                out["counted"], out["labels"] = r[0], r[1]
            lo_ts, hi_ts = utc_to_ts(lo), utc_to_ts(hi)
            out["new_catches"] = sum(1 for rows in self.catalog()["by"].values() if lo_ts <= rows[0]["ts"] < hi_ts)
            return out
        return self._cached("today", build)

    def activity(self, days: int = 1) -> dict:
        """Visible sightings per local hour of today (days=1) or per local day for the last `days` days."""
        def build():
            today = date.today()
            if days == 1:
                lo, hi = local_day_bounds_utc(today)
                start = datetime.combine(today, datetime.min.time())
                buckets = [start.replace(hour=hr) for hr in range(24)]
                unit = "hour"
            else:
                lo = local_day_bounds_utc(date.fromordinal(today.toordinal() - days + 1))[0]
                hi = local_day_bounds_utc(today)[1]
                buckets = [datetime.combine(date.fromordinal(today.toordinal() - k), datetime.min.time())
                           for k in range(days - 1, -1, -1)]
                unit = "day"
            counts = [0] * len(buckets)
            with self._open() as (con, eff):
                if con is not None:
                    for (at,) in con.execute(f"WITH {eff} SELECT started_at FROM eff WHERE NOT hidden "
                                             "AND started_at >= ? AND started_at < ?", (lo, hi)):
                        t = datetime.fromtimestamp(utc_to_ts(at))
                        i = t.hour if unit == "hour" else (t.date() - buckets[0].date()).days
                        if 0 <= i < len(counts):
                            counts[i] += 1
            return {"unit": unit, "items": [{"start": iso(b.timestamp()), "count": n} for b, n in zip(buckets, counts)]}
        return self._cached(f"activity{days}", build)
