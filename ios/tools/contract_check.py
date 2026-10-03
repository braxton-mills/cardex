#!/usr/bin/env python3
"""Check a running Campi API against docs/api-contract.md. Stdlib only.

    python3 tools/contract_check.py http://127.0.0.1:8765 --token <token>
    python3 tools/contract_check.py https://campi-pc.tail1234.ts.net --token <token> --skip-live
    python3 tools/contract_check.py --fixtures          # only validate contract/fixtures against schema.json

Use a token from a device paired just for this check (`campi pair`): the check changes that device's push
settings. Stars, hides and label corrections it makes on sightings, clips and daily videos are restored
afterwards (use --read-only to skip them). Exit code 0 = compliant.
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campi_contract import Schema, check_fixtures  # noqa: E402


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Checker:
    def __init__(self, base: str, token: str, read_only: bool, skip_live: bool, verbose: bool):
        self.base, self.token = base.rstrip("/"), token
        self.read_only, self.skip_live, self.verbose = read_only, skip_live, verbose
        self.schema = Schema.load()
        self.passed = self.failed = 0
        self.failures: list[str] = []

    # -- reporting
    def ok(self, cond: bool, what: str, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            if self.verbose:
                print(f"  ok   {what}")
        else:
            self.failed += 1
            msg = f"{what}" + (f": {detail}" if detail else "")
            self.failures.append(msg)
            print(f"  FAIL {msg}")
        return cond

    def shape(self, obj, typ: str, what: str) -> bool:
        errs = self.schema.validate(obj, typ)
        return self.ok(not errs, f"{what} matches {typ}", "; ".join(errs[:8]) + (" ..." if len(errs) > 8 else ""))

    # -- HTTP
    def req(self, method: str, path: str, *, auth=True, body=None, headers=None, params=None):
        url = path if path.startswith("http") else self.base + path
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params, doseq=True)
        h = dict(headers or {})
        if auth:
            h["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with OPENER.open(r, timeout=30) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def json_req(self, method, path, what, expect=200, **kw):
        status, headers, raw = self.req(method, path, **kw)
        if not self.ok(status == expect, f"{what} -> {expect}", f"got {status}: {raw[:300]!r}"):
            return None
        if expect == 204:
            return {}
        self.ok("application/json" in (headers.get("Content-Type") or ""), f"{what} is application/json")
        try:
            return json.loads(raw)
        except ValueError:
            self.ok(False, f"{what} body is JSON", raw[:200].decode(errors="replace"))
            return None

    def error_req(self, method, path, what, status, code, **kw):
        st, headers, raw = self.req(method, path, **kw)
        self.ok(st == status, f"{what} -> {status}", f"got {st}: {raw[:200]!r}")
        try:
            obj = json.loads(raw)
        except ValueError:
            self.ok(False, f"{what} error body is JSON", raw[:200].decode(errors="replace"))
            return
        if self.shape(obj, "Error", f"{what} error body"):
            self.ok(obj["error"]["code"] == code, f"{what} error code {code}", f"got {obj['error']['code']}")

    # -- checks
    def run(self) -> int:
        sections = [("auth", self.check_auth), ("status", self.check_status), ("devices", self.check_devices),
                    ("sightings", self.check_sightings), ("collection", self.check_collection),
                    ("highlights", self.check_highlights), ("clips", self.check_clips),
                    ("daily", self.check_daily), ("archive", self.check_archive), ("seek", self.check_seek),
                    ("media", self.check_media)]
        if not self.skip_live:
            sections.append(("live", self.check_live))
        for name, fn in sections:
            print(f"[{name}]")
            try:
                fn()
            except Exception as e:   # a crash in one section must not hide the others
                self.ok(False, f"{name} section crashed", f"{type(e).__name__}: {e}")
        print(f"\n{self.passed} passed, {self.failed} failed")
        return 0 if self.failed == 0 else 1

    def check_auth(self):
        self.error_req("GET", "/api/status", "no token", 401, "unauthorized", auth=False)
        self.error_req("GET", "/api/status", "bogus token", 401, "unauthorized", auth=False,
                       headers={"Authorization": "Bearer not-a-real-token"})
        self.error_req("POST", "/api/pair", "pair with a wrong code", 401, "invalid_code", auth=False,
                       body={"code": "0000-0000", "device_name": "contract check", "platform": "other"})
        self.error_req("GET", "/api/no-such-endpoint", "unknown route", 404, "not_found")

    def check_status(self):
        st = self.json_req("GET", "/api/status", "GET /api/status")
        if st is None or not self.shape(st, "Status", "status"):
            return
        self.status = st
        self.ok(st["api_version"] == 1, "api_version is 1", str(st["api_version"]))
        skew = abs(parse_dt(st["server_time"]) - time.time())
        self.ok(skew < 300, "server_time within 5 min of this machine", f"{skew:.0f}s")
        s = st["sightings"]
        self.ok(s["enabled"] or s["state"] == "disabled", "sightings disabled <=> state disabled")
        self.ok(s["today"] <= s["total"], "sightings today <= total")
        if st["service"]["state"] == "running":
            self.ok(st["service"]["pid"] is not None, "running service has a pid")

    def check_devices(self):
        me = self.json_req("GET", "/api/devices/me", "GET /api/devices/me")
        if me is None or not self.shape(me, "Device", "device"):
            return
        self.error_req("PUT", "/api/devices/me/push", "push prefs with a missing key", 400, "invalid_param",
                       body={"apns_token": None, "environment": "sandbox", "prefs": {"new_catch": True}})
        if self.read_only:
            return
        prefs = {"new_catch": True, "rare": True, "discovered": False, "service_alerts": True}
        d = self.json_req("PUT", "/api/devices/me/push", "PUT /api/devices/me/push",
                          body={"apns_token": "00" * 32, "environment": "sandbox", "prefs": prefs})
        if d and self.shape(d, "Device", "device after PUT push"):
            self.ok(d["push"]["prefs"] == prefs and d["push"]["enabled"], "push prefs stored")
        d = self.json_req("PUT", "/api/devices/me/push", "PUT /api/devices/me/push (off)",
                          body={"apns_token": None, "environment": "sandbox", "prefs": prefs})
        if d:
            self.ok(not d["push"]["enabled"], "apns_token null turns push off")

    def check_sightings(self):
        p1 = self.json_req("GET", "/api/sightings", "GET /api/sightings", params={"limit": 5})
        if p1 is None or not self.shape(p1, "SightingPage", "sightings page 1"):
            return
        self.sample = p1["items"]
        self.ok(p1["total"] >= len(p1["items"]), "total >= items on the page")
        self.ok(len(p1["items"]) <= 5, "limit respected")
        self.check_order(p1["items"], lambda s: (parse_dt(s["started_at"]), s["id"]), "sightings")
        if p1["next_cursor"]:
            p2 = self.json_req("GET", "/api/sightings", "GET /api/sightings page 2",
                               params={"limit": 5, "cursor": p1["next_cursor"]})
            if p2 and self.shape(p2, "SightingPage", "sightings page 2"):
                ids1 = {s["id"] for s in p1["items"]}
                self.ok(not ids1 & {s["id"] for s in p2["items"]}, "page 2 doesn't repeat page 1")
                self.check_order(p1["items"] + p2["items"], lambda s: (parse_dt(s["started_at"]), s["id"]),
                                 "sightings across pages")
        self.error_req("GET", "/api/sightings", "limit=0", 400, "invalid_param", params={"limit": 0})
        self.error_req("GET", "/api/sightings", "limit=201", 400, "invalid_param", params={"limit": 201})
        self.error_req("GET", "/api/sightings", "bad cursor", 400, "invalid_param", params={"cursor": "%%%"})
        self.error_req("GET", "/api/sightings", "bad from date", 400, "invalid_param", params={"from": "yesterday"})
        if not p1["items"]:
            print("  (no sightings: filter, detail and action checks skipped)")
            return
        for s in p1["items"]:
            self.ok(not s["hidden"], "hidden sightings excluded by default", s["id"])
            self.ok(s["decided_by"] == ("user" if s["correction"] else s["machine"]["source"]),
                    "decided_by follows correction / machine.source", s["id"])
        day = p1["items"][0]["day"]
        f = self.json_req("GET", "/api/sightings", "filter from/to", params={"from": day, "to": day, "limit": 50})
        if f:
            self.ok(all(s["day"] == day for s in f["items"]), "from/to keeps only that day")
        for params, pred, what in [
            ({"hide_stationary": "true"}, lambda s: not s["stationary"], "hide_stationary"),
            ({"hide_unsure": "true"}, lambda s: not s["unsure"] or s["decided_by"] != "siglip", "hide_unsure"),
            ({"decided_by": "cloud"}, lambda s: s["decided_by"] == "cloud", "decided_by=cloud"),
            ({"starred": "true"}, lambda s: s["starred"], "starred=true"),
            ({"class": "car"}, lambda s: s["yolo_class"] == "car", "class=car"),
        ]:
            r = self.json_req("GET", "/api/sightings", f"filter {what}", params={**params, "limit": 50})
            if r:
                self.ok(all(pred(s) for s in r["items"]), f"filter {what} holds", str(len(r["items"])))
        first = p1["items"][0]
        d = self.json_req("GET", f"/api/sightings/{first['id']}", "GET /api/sightings/{id}")
        if d and self.shape(d, "Sighting", "sighting detail"):
            self.ok(d["id"] == first["id"], "detail id matches")
        self.error_req("GET", f"/api/sightings/{uuid.uuid4()}", "unknown sighting", 404, "not_found")
        if not self.read_only:
            self.sighting_actions(first)

    def sighting_actions(self, s):
        sid, path = s["id"], f"/api/sightings/{s['id']}"
        r = self.json_req("POST", f"{path}/star", "star", body={"starred": not s["starred"]})
        if r and self.shape(r, "Sighting", "star response"):
            self.ok(r["starred"] == (not s["starred"]), "star sets state")
        r = self.json_req("POST", f"{path}/star", "star again (idempotent)", body={"starred": not s["starred"]})
        if r:
            self.ok(r["starred"] == (not s["starred"]), "star is a set, not a toggle")
        self.json_req("POST", f"{path}/star", "star restore", body={"starred": s["starred"]})
        self.error_req("POST", f"{path}/star", "star with a bad body", 400, "invalid_param", body={"starred": "yes"})

        r = self.json_req("POST", f"{path}/hide", "hide", body={"hidden": True})
        if r and self.shape(r, "Sighting", "hide response"):
            self.ok(r["hidden"], "hide sets state")
            lst = self.json_req("GET", "/api/sightings", "list after hide",
                                params={"from": s["day"], "to": s["day"], "limit": 200})
            if lst:
                self.ok(sid not in {x["id"] for x in lst["items"]}, "hidden sighting left the default list")
            lst = self.json_req("GET", "/api/sightings", "list include_hidden",
                                params={"from": s["day"], "to": s["day"], "limit": 200, "include_hidden": "true"})
            if lst:
                self.ok(sid in {x["id"] for x in lst["items"]}, "include_hidden shows it")
        self.json_req("POST", f"{path}/hide", "hide restore", body={"hidden": False})

        coll = self.json_req("GET", "/api/collection", "collection (for label check)")
        if coll:
            other = next((i["label"] for i in coll["items"] if i["label"] != s["label"]), None)
            if other:
                r = self.json_req("POST", f"{path}/label", "label correction", body={"label": other})
                if r and self.shape(r, "Sighting", "label response"):
                    self.ok(r["label"] == other and r["decided_by"] == "user" and r["correction"] is not None
                            and r["correction"]["label"] == other, "correction applied as effective label")
                    self.ok(r["machine"]["label"] == s["machine"]["label"], "machine verdict unchanged")
        self.error_req("POST", f"{path}/label", "unknown label", 422, "unknown_label",
                       body={"label": "Definitely Not A Car 9000"})
        restore = s["correction"]["label"] if s["correction"] else None
        r = self.json_req("POST", f"{path}/label", "label restore", body={"label": restore})
        if r:
            self.ok(r["label"] == s["label"] and r["decided_by"] == s["decided_by"], "label restored")

    def check_collection(self):
        c = self.json_req("GET", "/api/collection", "GET /api/collection")
        if c is None or not self.shape(c, "Collection", "collection"):
            return
        items = c["items"]
        self.ok(c["total"] == len(items), "total == number of items")
        self.ok(c["caught"] == sum(1 for i in items if i["count"] > 0), "caught == items with count > 0")
        self.ok(len({i["label"] for i in items}) == len(items), "labels are unique")

        def tier_of(n):
            if n == 0:
                return "uncaught"
            for t in c["tiers"]:
                if n >= t["min"] and (t["max"] is None or n <= t["max"]):
                    return t["tier"]
            return None
        for i in items:
            self.ok(i["tier"] == tier_of(i["count"]), "tier matches count and thresholds", f"{i['label']}")
            self.ok((i["cover"] is None) == (i["count"] == 0), "cover iff caught", i["label"])
            self.ok((i["first_seen_at"] is None) == (i["count"] == 0), "first_seen_at iff caught", i["label"])
        origins = [i["origin"] for i in items]
        order = {"labels_file": 0, "discovered": 1, "other": 2}
        self.ok(origins == sorted(origins, key=order.get), "labels_file, then discovered, then other")

    def check_highlights(self):
        h = self.json_req("GET", "/api/highlights", "GET /api/highlights", params={"limit": 50})
        if h is None or not self.shape(h, "HighlightPage", "highlights"):
            return
        self.check_order(h["items"], lambda x: (parse_dt(x["at"]), x["id"]), "highlights")
        for x in h["items"]:
            kind = x["id"].split(":", 1)[0]
            filled = {k for k in ("sighting", "clip", "daily", "window") if x[k] is not None}
            expect = {"sighting": {"sighting"}, "clip": {"clip"}, "daily": {"daily"}}.get(kind)
            if kind == "window":
                self.ok("window" in filled and filled <= {"window", "clip"}, "window item shape", x["id"])
            else:
                self.ok(filled == expect, f"{kind} item has exactly its object", f"{x['id']}: {sorted(filled)}")
            self.ok(len(x["types"]) == len(set(x["types"])) > 0, "types non-empty and unique", x["id"])
        self.ok(len({x["id"] for x in h["items"]}) == len(h["items"]), "one item per thing")
        d = self.json_req("GET", "/api/highlights", "highlights type=daily", params={"type": "daily", "limit": 20})
        if d:
            self.ok(all("daily" in x["types"] for x in d["items"]), "type filter holds")
        self.error_req("GET", "/api/highlights", "unknown highlight type", 400, "invalid_param",
                       params={"type": "fancy"})

    def check_clips(self):
        c = self.json_req("GET", "/api/clips", "GET /api/clips")
        if c is None or not self.shape(c, "ClipList", "clips"):
            return
        self.clips = c["items"]
        ids = [x["id"] for x in c["items"]]
        self.ok(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}", i) for i in ids), "clip ids look like the file names")
        self.check_order(c["items"], lambda x: (parse_dt(x["window_start"]), x["id"]), "clips")
        self.ok(c["latest_id"] is None or c["latest_id"] in ids, "latest_id is a listed clip")
        for x in c["items"]:
            self.ok(parse_dt(x["window_end"]) > parse_dt(x["window_start"]), "window_end > window_start", x["id"])
            self.ok("latest" not in x["media"]["video"], "latest.mp4 never served", x["id"])
        self.error_req("GET", "/api/clips/2001-01-01_0000", "unknown clip", 404, "not_found")
        if not c["items"]:
            return
        x = c["items"][0]
        d = self.json_req("GET", f"/api/clips/{x['id']}", "GET /api/clips/{id}")
        if d:
            self.shape(d, "Clip", "clip detail")
        if not self.read_only:
            r = self.json_req("POST", f"/api/clips/{x['id']}/star", "clip star", body={"starred": not x["starred"]})
            if r and self.shape(r, "Clip", "clip star response"):
                self.ok(r["starred"] == (not x["starred"]), "clip star sets state")
            self.json_req("POST", f"/api/clips/{x['id']}/star", "clip star restore", body={"starred": x["starred"]})

    def check_daily(self):
        d = self.json_req("GET", "/api/daily", "GET /api/daily", params={"limit": 3})
        if d is None or not self.shape(d, "DailyPage", "daily page"):
            return
        self.dailies = d["items"]
        self.check_order(d["items"], lambda x: (x["day"],), "daily")
        if d["next_cursor"]:
            d2 = self.json_req("GET", "/api/daily", "GET /api/daily page 2", params={"limit": 3,
                                                                                    "cursor": d["next_cursor"]})
            if d2:
                self.ok(not {x["day"] for x in d["items"]} & {x["day"] for x in d2["items"]}, "daily pages disjoint")
        self.error_req("GET", "/api/daily/1999-01-01", "unknown daily", 404, "not_found")
        if d["items"]:
            x = d["items"][0]
            g = self.json_req("GET", f"/api/daily/{x['day']}", "GET /api/daily/{day}")
            if g:
                self.shape(g, "Daily", "daily detail")
            if not self.read_only:
                r = self.json_req("POST", f"/api/daily/{x['day']}/star", "daily star",
                                  body={"starred": not x["starred"]})
                if r:
                    self.ok(r["starred"] == (not x["starred"]), "daily star sets state")
                self.json_req("POST", f"/api/daily/{x['day']}/star", "daily star restore",
                              body={"starred": x["starred"]})

    def check_archive(self):
        a = self.json_req("GET", "/api/archive", "GET /api/archive")
        if a is None or not self.shape(a, "ArchiveList", "archive"):
            return
        parts = [x["part"] for x in a["items"]]
        self.ok(parts == sorted(parts, reverse=True), "archive parts highest first")
        cur = [x for x in a["items"] if x["current"]]
        self.ok(len(cur) <= 1, "at most one current part")
        for x in a["items"]:
            self.ok((x["media"]["video"] is None) == x["current"], "only the current part has no video", str(x["part"]))
        if cur and a["items"]:
            self.ok(cur[0]["part"] == max(parts), "current part is the highest")
        if cur:
            st, _, _ = self.req("GET", f"/media/archive/campi_archive_{cur[0]['part']:03d}.mp4")
            self.ok(st == 404, "current archive part is never served", f"got {st}")

    def check_seek(self):
        self.error_req("GET", "/api/seek", "seek without ts", 400, "invalid_param")
        self.error_req("GET", "/api/seek", "seek in the future", 400, "invalid_param",
                       params={"ts": str(time.time() + 86400)})
        probes = []
        for x in getattr(self, "clips", [])[:2]:
            mid = (parse_dt(x["window_start"]) + parse_dt(x["window_end"])) / 2
            probes.append((mid, "clip", x["id"]))
        for s in getattr(self, "sample", [])[:2]:
            probes.append(((parse_dt(s["started_at"]) + parse_dt(s["ended_at"])) / 2, None, None))
        probes.append((time.time() - 400 * 86400, None, None))
        for ts, want, cid in probes:
            r = self.json_req("GET", "/api/seek", "GET /api/seek", params={"ts": f"{ts:.3f}"})
            if r is None or not self.shape(r, "Seek", "seek"):
                continue
            if want:
                self.ok(r["target"] == "clip" and r["clip_id"] == cid, "instant inside a clip seeks into it",
                        f"{r['target']} {r['clip_id']} (wanted {cid})")
                self.ok(r["approximate"] is False, "clip seek is exact")
            if r["target"] == "none":
                self.ok(r["reason"] is not None and r["video"] is None and r["offset_s"] is None,
                        "none has a reason and no video")
            else:
                self.ok(r["video"] is not None and r["offset_s"] is not None and r["offset_s"] >= 0,
                        "target has a video and an offset")
                self.ok(r["reason"] is None, "no reason when a target was found")
        r = self.json_req("GET", "/api/seek", "seek with an RFC 3339 ts",
                          params={"ts": datetime.now().astimezone().isoformat(timespec="seconds")})
        if r:
            self.shape(r, "Seek", "seek (RFC 3339)")

    def check_media(self):
        targets = []
        for x in getattr(self, "clips", [])[:1]:
            targets.append((x["media"]["video"], "video/mp4", x["size_bytes"]))
            targets.append((x["media"]["poster"], "image/jpeg", None))
        for x in getattr(self, "dailies", [])[:1]:
            targets.append((x["media"]["video"], "video/mp4", x["size_bytes"]))
        for s in getattr(self, "sample", [])[:1]:
            targets.append((s["media"]["crop"], "image/jpeg", None))
        if not targets:
            print("  (no media to check)")
            return
        for url, ctype, size in targets:
            self.check_one_media(url, ctype, size)
        url = targets[0][0]
        path, query = url.split("?", 1)
        tampered = re.sub(r"sig=[^&]+", "sig=AAAA", url)
        self.error_req("GET", tampered, "tampered signature", 401, "unauthorized", auth=False)
        self.error_req("GET", path, "media without token or signature", 401, "unauthorized", auth=False)
        st, h, _ = self.req("HEAD", path)
        self.ok(st == 200, "media with bearer and no signature", f"got {st}")
        st, _, _ = self.req("GET", "/media/sightings/..%2F..%2Fstate%2Fstatus.json")
        self.ok(st in (404, 400), "path traversal refused", f"got {st}")
        st, _, _ = self.req("GET", "/media/clips/latest.mp4")
        self.ok(st == 404, "latest.mp4 not served", f"got {st}")

    def check_one_media(self, url: str, ctype: str, size: int | None):
        what = url.split("?")[0]
        st, h, _ = self.req("HEAD", url, auth=False)
        if not self.ok(st == 200, f"HEAD {what} (signed, no bearer)", f"got {st}"):
            return
        self.ok((h.get("Content-Type") or "").startswith(ctype), f"{what} Content-Type {ctype}", h.get("Content-Type"))
        self.ok(h.get("Accept-Ranges") == "bytes", f"{what} Accept-Ranges: bytes")
        self.ok(bool(h.get("ETag")), f"{what} has ETag")
        self.ok("immutable" not in (h.get("Cache-Control") or ""), f"{what} not immutable")
        length = int(h.get("Content-Length") or -1)
        if size is not None:
            self.ok(length == size, f"{what} Content-Length == size_bytes", f"{length} vs {size}")
        st, h2, body = self.req("GET", url, auth=False, headers={"Range": "bytes=0-99"})
        self.ok(st == 206 and len(body) == 100, f"{what} Range bytes=0-99 -> 206, 100 bytes", f"{st}, {len(body)}")
        self.ok(h2.get("Content-Range") == f"bytes 0-99/{length}", f"{what} Content-Range",
                str(h2.get("Content-Range")))
        st, _, body = self.req("GET", url, auth=False, headers={"Range": "bytes=-10"})
        self.ok(st == 206 and len(body) == 10, f"{what} suffix range", f"{st}, {len(body)}")
        st, _, _ = self.req("GET", url, auth=False, headers={"Range": f"bytes={length + 10}-"})
        self.ok(st == 416, f"{what} unsatisfiable range -> 416", f"got {st}")
        st, _, _ = self.req("GET", url, auth=False, headers={"If-None-Match": h.get("ETag") or ""})
        self.ok(st == 304, f"{what} If-None-Match -> 304", f"got {st}")

    def check_live(self):
        live = getattr(self, "status", {}).get("live") if hasattr(self, "status") else None
        if not live or not live["available"]:
            print("  (live not available: skipped)")
            return
        st, h, body = self.req("GET", live["snapshot"], auth=False)
        if st == 200:
            self.ok((h.get("Content-Type") or "").startswith("image/jpeg"), "live.jpg is a JPEG")
            self.ok(body[:2] == b"\xff\xd8", "live.jpg starts with SOI")
            xf = h.get("X-Frame-Time")
            self.ok(bool(xf) and not self.schema.validate(xf, "datetime"), "live.jpg X-Frame-Time", str(xf))
            self.ok("no-store" in (h.get("Cache-Control") or ""), "live.jpg Cache-Control no-store")
        else:
            self.ok(st in (404, 429), "live.jpg -> 200 (or 404 no recent frame / 429)", f"got {st}")
        self.check_mjpeg(live["mjpeg"])

    def check_mjpeg(self, url: str):
        u = urlsplit(self.base + url + "&max_fps=5")
        conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(u.hostname, u.port, timeout=15)
        try:
            conn.request("GET", urlunsplit(("", "", u.path, u.query, "")))
            r = conn.getresponse()
            ctype = r.getheader("Content-Type") or ""
            if r.status in (502, 503):
                self.ok(True, f"live.mjpg answered {r.status} (Pi unreachable / busy)")
                return
            self.ok(r.status == 200, "live.mjpg -> 200", f"got {r.status}")
            m = re.search(r"boundary=\"?([^\";]+)", ctype)
            self.ok(ctype.startswith("multipart/x-mixed-replace") and m is not None,
                    "live.mjpg is multipart/x-mixed-replace with a boundary", ctype)
            if not m:
                return
            buf, t0 = b"", time.time()
            marker = b"--" + m.group(1).encode()
            while buf.count(marker) < 3 and time.time() - t0 < 10 and len(buf) < 8_000_000:
                chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                if not chunk:
                    break
                buf += chunk
            self.ok(buf.count(marker) >= 2, "live.mjpg delivers at least 2 frames within 10 s")
            self.ok(b"\xff\xd8" in buf and b"Content-Length:" in buf, "parts are JPEGs with Content-Length")
        finally:
            conn.close()   # the server must close its upstream now (check the PC log)

    # -- helpers
    def check_order(self, items, key, what):
        keys = [key(x) for x in items]
        self.ok(keys == sorted(keys, reverse=True), f"{what} newest first")


def parse_dt(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", nargs="?", help="base URL, e.g. http://127.0.0.1:8765")
    ap.add_argument("--token")
    ap.add_argument("--fixtures", action="store_true", help="only validate contract/fixtures")
    ap.add_argument("--read-only", action="store_true", help="skip checks that change state")
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.fixtures or not a.base:
        errs = check_fixtures()
        print("\n".join(errs) or "fixtures: all valid")
        if not a.base:
            return 1 if errs else 0
    if not a.token:
        ap.error("--token is required")
    return Checker(a.base, a.token, a.read_only, a.skip_live, a.verbose).run()


if __name__ == "__main__":
    sys.exit(main())
