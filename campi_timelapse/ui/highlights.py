"""GET /api/highlights (contract §4.7, §5.7, §6.3), computed at query time from counted sightings, clips, daily
videos and stars. One item per thing, with every type that applies:
- new_catch: the earliest counted sighting of each label; rare: every counted sighting of a label seen 1-3 times;
- busiest: per local day, the 3 grid windows with the most counted sightings (at least 3);
- daily: every daily video (at = the next local midnight); starred: any starred sighting, clip or daily video.
  A starred clip whose file is gone (or that is also a busiest window) is a window: item.
"""
from __future__ import annotations

from datetime import date, timedelta

from .contract import date_param, iso, local_day, midnight, multi, page
from .library import parse_clip_id

TYPES = ("new_catch", "rare", "busiest", "daily", "starred")
RARE_MAX = 3
BUSY_PER_DAY, BUSY_MIN = 3, 3


def window_id(ts: float) -> str:
    from datetime import datetime
    return f"{datetime.fromtimestamp(ts):%Y-%m-%d_%H%M}"


class Highlights:
    def __init__(self, cfg, store, sightings, library):
        self.cfg, self.store, self.sd, self.lib = cfg, store, sightings, library

    def all(self, signer) -> list[dict]:
        out: dict[str, dict] = {}

        def add(hid, typ, at, day, **kw):
            h = out.setdefault(hid, {"id": hid, "types": [], "at": at, "day": day, "sighting": None, "clip": None,
                                     "daily": None, "window": None})
            if typ not in h["types"]:
                h["types"].append(typ)
            for k, v in kw.items():
                if v is not None:
                    h[k] = v

        cat = self.sd.catalog()
        sighting_ids: dict[str, list[str]] = {}   # id -> types
        for rows in cat["by"].values():
            sighting_ids.setdefault(rows[0]["id"], []).append("new_catch")
            if len(rows) <= RARE_MAX:
                for s in rows:
                    sighting_ids.setdefault(s["id"], []).append("rare")
        for sid in self.store.stars("sighting"):
            sighting_ids.setdefault(sid, []).append("starred")
        rows = self.sd.rows_by_id(list(sighting_ids))
        for sid, types in sighting_ids.items():
            r = rows.get(sid)
            if r is None:
                continue  # starred, then deleted from sightings.db
            view = self.sd.view(r, signer)
            at = self._ts(view["started_at"])
            for t in types:
                add(f"sighting:{sid}", t, at, view["day"], sighting=view)

        clips = {c["id"]: c for c in self.lib.clip_files()}
        clip_stars = self.store.stars("clip")

        def window_item(cid: str, typ: str, start: float):
            step = int(self.cfg.render.interval_min) * 60
            end = start + step
            c = clips.get(cid)
            add(f"window:{cid}", typ, start, local_day(start).isoformat(),
                clip=self.lib.clip_json(c, signer, clip_stars) if c else None,
                window={"start": iso(start), "end": iso(end),
                        "sightings_count": self.sd.count_between(start, end) or 0})

        # busiest windows, aligned like Supervisor.next_boundary (local midnight + k * interval_min)
        step = int(self.cfg.render.interval_min) * 60
        per_window: dict[float, int] = {}
        for rows_ in cat["by"].values():
            for s in rows_:
                m = midnight(local_day(s["ts"]))
                w = m + (s["ts"] - m) // step * step
                per_window[w] = per_window.get(w, 0) + 1
        per_day: dict[date, list] = {}
        for w, n in per_window.items():
            if n >= BUSY_MIN:
                per_day.setdefault(local_day(w), []).append((n, w))
        busiest = set()
        for lst in per_day.values():
            for n, w in sorted(lst, reverse=True)[:BUSY_PER_DAY]:
                cid = window_id(w)
                busiest.add(cid)
                window_item(cid, "busiest", w)

        stars_daily = self.store.stars("daily")
        for p, d, stt in self.lib.daily_files():
            at = midnight(d + timedelta(days=1))
            dj = self.lib.daily_json(p, d, stt, signer, stars_daily)
            add(f"daily:{d.isoformat()}", "daily", at, d.isoformat(), daily=dj)
            if d.isoformat() in stars_daily:
                add(f"daily:{d.isoformat()}", "starred", at, d.isoformat(), daily=dj)

        for cid in clip_stars:
            c = clips.get(cid)
            if c and cid not in busiest:
                add(f"clip:{cid}", "starred", c["start"], local_day(c["start"]).isoformat(),
                    clip=self.lib.clip_json(c, signer, clip_stars))
            else:
                start = c["start"] if c else parse_clip_id(cid)
                if start is not None:
                    window_item(cid, "starred", start)

        items = list(out.values())
        for h in items:
            h["types"] = [t for t in TYPES if t in h["types"]]
        return items

    @staticmethod
    def _ts(s: str) -> float:
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()

    def page(self, q, signer) -> dict:
        types = set(multi(q, "type", TYPES))
        frm, to = date_param(q, "from"), date_param(q, "to")
        items = [h for h in self.all(signer)
                 if (not types or types & set(h["types"]))
                 and (not frm or h["day"] >= frm.isoformat()) and (not to or h["day"] <= to.isoformat())]
        items.sort(key=lambda h: (round(h["at"], 3), h["id"]), reverse=True)
        pg = page(items, lambda h: [round(h["at"], 3), h["id"]], q)
        return {"items": [{**h, "at": iso(h["at"])} for h in pg["items"]], "next_cursor": pg["next_cursor"],
                "total": pg["total"]}
