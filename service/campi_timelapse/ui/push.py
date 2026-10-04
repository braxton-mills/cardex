"""APNs push notifications (contract §9). Runs only inside `campi_timelapse api` with [api.push] enabled and the key
file present; a server that `campi ui` starts for itself never sends.

Sightings: every ~10 s, rows after a rowid high-water mark (kept in ui.db; on the very first run it starts at the
current maximum, so nothing old is pushed). A row whose Gemini answer is still pending (cloud on, under the daily
cap, queued less than 60 s ago) stops the scan, so the mark never passes it and the push names Gemini's label.
Each counted sighting becomes at most one notification per device: the most specific event its prefs want of
discovered (Gemini named a car that isn't in the labels file), new_catch (first of its label), rare (2nd or 3rd).
Discovered labels that arrive after their sighting was processed are picked up by their own rowid mark.

Service alerts fire once per incident and once more (`recovered`) when it clears; the open incidents are kept in
ui.db so a restart doesn't repeat them. `pushes(sighting_id, type)` makes every push happen at most once.

The .p8 key is read only to sign the JWT (reused ~30 min) and never logged. A device token answering 410 is cleared.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from datetime import datetime
from urllib.parse import quote

from ..config import read_json
from .contract import local_day, utc_to_ts

log = logging.getLogger("ui.push")

HOSTS = {"sandbox": "api.sandbox.push.apple.com", "production": "api.push.apple.com"}
JWT_TTL_S = 30 * 60
CLOUD_WAIT_S = 60
NO_FRAME_S = 600
ALERT_TEXT = {
    "capture_disconnected": ("Campi: camera offline", "No frame saved for 10 minutes."),
    "sightings_crash_looping": ("Campi: sightings keep crashing", "The sightings worker is crash-looping."),
    "disk_low": ("Campi: disk almost full", "Free space is below the configured minimum."),
    "render_failing": ("Campi: renders failing", "The last two 10-minute clips failed to render."),
}
RECOVERED_TEXT = {
    "capture_disconnected": "The camera is saving frames again.",
    "sightings_crash_looping": "The sightings worker is running again.",
    "disk_low": "Free space is back above the minimum.",
    "render_failing": "Clips are rendering again.",
}
DIRECTION = {"LR": "left to right", "RL": "right to left"}


def ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


class PushSender:
    def __init__(self, cfg, store, sightings, transport=None, now=time.time):
        self.cfg, self.store, self.sd = cfg, store, sightings
        self.p = cfg.api.push
        self.transport = transport
        self.now = now
        self._client = None
        self._jwt: tuple[str, float] | None = None

    # ------------------------------------------------------------ loop

    def start(self, stop: threading.Event) -> threading.Thread:
        t = threading.Thread(target=self.run, args=(stop,), daemon=True, name="push")
        t.start()
        return t

    def run(self, stop: threading.Event) -> None:
        log.info("push sender started (%s)", ", ".join(sorted({d["apns_env"] or "?" for d in self.targets()})) or
                 "no devices with push yet")
        while not stop.wait(10):
            self.tick()

    def tick(self) -> None:
        for step in (self.sightings_tick, self.discovered_tick, self.alerts_tick):
            try:
                step()
            except Exception:
                log.exception("push: %s failed", step.__name__)

    def targets(self, pref: str | None = None) -> list[dict]:
        return [d for d in self.store.devices() if d["apns_token"] and (pref is None or d["prefs"].get(pref))]

    # ------------------------------------------------------------ sightings

    def _cloud_open(self) -> bool:
        """Is Gemini going to answer new rows soon (enabled and under today's cap)?"""
        c = self.cfg.sightings.cloud
        if not (self.cfg.sightings.enabled and c.enabled):
            return False
        ws = read_json(self.cfg.paths.state / "sightings.json", {}) or {}
        return int(ws.get("cloud_today") or 0) < int(c.cloud_max_per_day)

    def sightings_tick(self) -> None:
        mark = self.store.get("push.mark")
        with self.sd._open() as (con, eff):
            if con is None:
                return
            if mark is None:
                top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM main.sightings").fetchone()[0]
                self.store.put("push.mark", top)
                log.info("push: first run; starting after sighting rowid %d (nothing old is pushed)", top)
                return
            rows = con.execute(f"WITH {eff} SELECT * FROM eff WHERE rowid > ? ORDER BY rowid LIMIT 200",
                               (mark,)).fetchall()
            queue = {}
            if rows and self.sd._has_table(con, "cloud_queue"):
                ids = [r["id"] for r in rows]
                queue = {q[0]: (q[1], q[2]) for q in con.execute(
                    f"SELECT sighting_id, created_at, done_at FROM main.cloud_queue WHERE sighting_id IN "
                    f"({', '.join('?' * len(ids))})", ids)}
            disc = ({r[0].lower(): r[1] for r in con.execute("SELECT label, first_seen FROM main.discovered_labels")}
                    if rows and self.sd._has_table(con, "discovered_labels") else {})
        if not rows:
            return
        cloud_open = self._cloud_open()
        now = self.now()
        for r in rows:
            q = queue.get(r["id"])
            if cloud_open and q and q[1] is None and now - (utc_to_ts(q[0]) or now) < CLOUD_WAIT_S:
                break  # wait for Gemini's answer (at most 60 s); the mark stays before this row
            self.process(r, disc)
            self.store.put("push.mark", r["rowid"])

    def counted(self, r) -> bool:
        return (not r["hidden"] and bool(r["label"]) and (not r["unsure"] or r["decided_by"] in ("cloud", "user")))

    def process(self, r, disc: dict) -> None:
        if not self.counted(r):
            return
        rows = self.sd.catalog()["by"].get(r["label"].lower(), [])
        nth = next((i + 1 for i, s in enumerate(rows) if s["id"] == r["id"]), None)
        if nth is None:
            return
        events = []
        if disc.get(r["label"].lower()) == r["started_at"]:
            events.append("discovered")
        if nth == 1:
            events.append("new_catch")
        elif nth <= 3:
            events.append("rare")
        if not events:
            return
        for dev in self.targets():
            ev = next((e for e in events if dev["prefs"].get(e)), None)
            if ev:
                self.send_sighting(dev, ev, r, nth)

    def discovered_tick(self) -> None:
        """discovered_labels rows added after their sighting was already processed (a slow Gemini answer)."""
        mark = self.store.get("push.discovered_mark")
        with self.sd._open() as (con, eff):
            if con is None or not self.sd._has_table(con, "discovered_labels"):
                return
            if mark is None:
                top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM main.discovered_labels").fetchone()[0]
                self.store.put("push.discovered_mark", top)
                return
            new = con.execute("SELECT rowid, label, first_seen FROM main.discovered_labels WHERE rowid > ? "
                              "ORDER BY rowid", (mark,)).fetchall()
            hits = []
            sight_mark = self.store.get("push.mark") or 0
            for d in new:
                r = con.execute(f"WITH {eff} SELECT * FROM eff WHERE started_at = ? AND label = ? COLLATE NOCASE",
                                (d[2], d[1])).fetchone()
                hits.append((d[0], r if r is not None and r["rowid"] <= sight_mark else None))
        for rowid, r in hits:
            if r is not None and self.counted(r):  # not yet scanned rows are handled by sightings_tick
                for dev in self.targets("discovered"):
                    self.send_sighting(dev, "discovered", r, None)
            self.store.put("push.discovered_mark", rowid)

    def payload(self, ev: str, r, nth: int | None) -> dict:
        label = self.sd.canon(r["label"])
        started = utc_to_ts(r["started_at"])
        title = {"new_catch": f"New catch: {label}", "discovered": f"Discovered: {label}",
                 "rare": f"Rare: {label}" + (f" ({ordinal(nth)} sighting)" if nth else "")}[ev]
        when = datetime.fromtimestamp(started).strftime("%I:%M %p").lstrip("0")
        where = "parked" if r["stationary"] else DIRECTION.get(r["raw_direction"], "")
        conf = r["raw_confidence"]
        who = {"cloud": "Gemini", "siglip": "SigLIP", "user": "your label"}[r["decided_by"]]
        if conf is not None and r["decided_by"] != "user":
            who += f" {round(conf * 100)}%"
        crop = f"/media/sightings/{quote(r['raw_crop_path'])}" if r["raw_crop_path"] else None
        return {"aps": {"alert": {"title": title, "body": " · ".join(x for x in (when, where, who) if x)},
                        "sound": "default", "mutable-content": 1, "thread-id": "sightings", "category": "SIGHTING"},
                "campi": {"type": ev, "sighting_id": r["id"], "label": label,
                          "day": local_day(started).isoformat(), "crop": crop, "alert": None}}

    def send_sighting(self, dev: dict, ev: str, r, nth: int | None) -> None:
        if not self.store.mark_pushed(f"{r['id']}:{dev['id']}", ev):
            return
        self.store.mark_pushed(r["id"], ev)
        self.send(dev, self.payload(ev, r, nth), collapse=f"{ev}-{r['id'][:40]}")

    # ------------------------------------------------------------ service alerts

    def dark(self, now: float) -> bool:
        """Night: at this clock time yesterday there was no usable frame within 15 minutes (else, the last saved
        frame today was below the night threshold)."""
        from .library import Library
        lib = Library(self.cfg, self.store, self.sd)
        y = lib.usable_day(local_day(now - 86400))
        if y is not None:
            return not any(abs(t - (now - 86400)) <= 900 for t in y)
        today = lib.usable_day(local_day(now))
        return not today or now - today[-1] > NO_FRAME_S

    def conditions(self, now: float) -> dict[str, bool]:
        st = read_json(self.cfg.paths.state / "status.json", {}) or {}
        alive = bool(st.get("supervisor_pid") and now - st.get("updated", 0) < 30)
        cap = read_json(self.cfg.paths.state / "capture.json", {}) or {}
        last = cap.get("last_frame_ts") or 0
        out = {
            "capture_disconnected": alive and now - last > NO_FRAME_S and not self.dark(now),
            "sightings_crash_looping": alive and bool(st.get("sightings_crash_looping")),
            "disk_low": shutil.disk_usage(self.cfg.paths.frames).free / 1e9 < float(self.cfg.retention.min_free_gb),
        }
        clip = read_json(self.cfg.paths.state / "last_clip.json", {}) or {}
        rs = self.store.get("push.render", {"finished": None, "errors": 0})
        if clip.get("finished") and clip.get("finished") != rs["finished"]:
            rs = {"finished": clip["finished"], "errors": rs["errors"] + 1 if clip.get("status") == "error" else 0}
            self.store.put("push.render", rs)
        out["render_failing"] = rs["errors"] >= 2
        return out

    def alerts_tick(self) -> None:
        now = self.now()
        open_ = self.store.get("push.alerts", {})
        changed = False
        for alert, active in self.conditions(now).items():
            since = open_.get(alert)
            if active and since is None:
                open_[alert] = now
                changed = True
                self.send_service(alert, f"service:{alert}:{int(now)}", ALERT_TEXT[alert], alert)
            elif not active and since is not None:
                del open_[alert]
                changed = True
                self.send_service(alert, f"service:{alert}:{int(since)}:recovered",
                                  ("Campi: recovered", RECOVERED_TEXT[alert]), "recovered")
        if changed:
            self.store.put("push.alerts", open_)

    def send_service(self, alert: str, key: str, text: tuple[str, str], kind: str) -> None:
        if not self.store.mark_pushed(key, "service"):
            return
        log.info("push: service alert %s (%s)", alert, kind)
        payload = {"aps": {"alert": {"title": text[0], "body": text[1]}, "sound": "default", "mutable-content": 1,
                           "thread-id": "service", "category": "SERVICE"},
                   "campi": {"type": "service", "sighting_id": None, "label": None, "day": None, "crop": None,
                             "alert": kind}}
        for dev in self.targets("service_alerts"):
            self.send(dev, payload, collapse=f"service-{alert}")

    # ------------------------------------------------------------ APNs

    def client(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(http2=True, timeout=15, transport=self.transport)
        return self._client

    def token(self, fresh: bool = False) -> str:
        now = self.now()
        if fresh or not self._jwt or now - self._jwt[1] > JWT_TTL_S:
            import jwt
            with open(self.p.key_file, encoding="utf-8") as f:
                key = f.read()
            self._jwt = (jwt.encode({"iss": self.p.team_id, "iat": int(now)}, key, algorithm="ES256",
                                    headers={"kid": self.p.key_id}), now)
        return self._jwt[0]

    def send(self, dev: dict, payload: dict, collapse: str | None = None) -> int | None:
        env = dev["apns_env"] if dev["apns_env"] in HOSTS else "production"
        url = f"https://{HOSTS[env]}/3/device/{dev['apns_token']}"
        for attempt in (0, 1):
            headers = {"authorization": f"bearer {self.token(fresh=attempt == 1)}", "apns-topic": self.p.bundle_id,
                       "apns-push-type": "alert", "apns-priority": "10"}
            if collapse:
                headers["apns-collapse-id"] = collapse[:64]
            try:
                r = self.client().post(url, json=payload, headers=headers)
            except Exception as e:
                log.warning("push to %s failed: %s", dev["id"], type(e).__name__)
                return None
            reason = ""
            if r.status_code != 200:
                try:
                    reason = r.json().get("reason", "")
                except ValueError:
                    pass
            if r.status_code == 403 and reason in ("ExpiredProviderToken", "InvalidProviderToken") and attempt == 0:
                continue  # sign a new JWT and retry once
            break
        if r.status_code == 200:
            log.info("push %s -> %s (%s, %s)", payload["campi"]["type"], dev["id"], dev["name"], env)
        elif r.status_code == 410:
            self.store.clear_apns_token(dev["id"], dev["apns_token"])
            log.info("push: %s's token is no longer registered (410); cleared", dev["id"])
        else:
            log.warning("push to %s: HTTP %d %s", dev["id"], r.status_code, reason)
        return r.status_code
