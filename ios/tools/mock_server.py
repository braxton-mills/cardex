#!/usr/bin/env python3
"""Mock Campi PC: implements docs/api-contract.md with synthetic data, for building the iOS app before the PC exists.

    python3 tools/mock_server.py                    # http://127.0.0.1:8765, dev token "mock-token"
    python3 tools/mock_server.py --host 0.0.0.0     # reachable from a phone on the LAN (debug builds only)
    python3 tools/mock_server.py --sightings-off --v1
    python3 tools/mock_server.py --write-fixtures   # regenerate contract/fixtures/ (fixed clock and seed)
    python3 tools/mock_server.py --print-push new_catch > /tmp/p.apns   # for `xcrun simctl push`
    curl -X POST http://127.0.0.1:8765/mock/pair-code                   # mock-only: issue a fresh pairing code
    python3 tools/mock_server.py --live busy --rotation 90               # exercise Live's error states / rotation
    curl -X POST -d '{"state":"unreachable"}' http://127.0.0.1:8765/mock/live   # mock-only: change it at runtime
    curl -X POST -d '{"type":"new_catch"}' http://127.0.0.1:8765/mock/push      # mock-only: simctl push to the simulator
    curl -X POST -d '{"seconds":60}' http://127.0.0.1:8765/mock/offline         # mock-only: drop API connections

Stdlib only (Python 3.11+). Sample media is generated once with ffmpeg into tools/mock_media/.
Mock limitations: every clip/daily/archive/sighting file of a kind is the same sample file; /live.jpg ignores w
beyond picking a 1080-wide sample; state (stars, hides, corrections, devices) lives in memory.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit
from zoneinfo import ZoneInfo

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from campi_contract import FIXTURES, Schema  # noqa: E402

MEDIA = TOOLS / "mock_media"

# Mirrors of the PC's config.toml defaults
INTERVAL_MIN = WINDOW_MIN = 10
DELAY_S = 15
RENDER_S = 60            # how long a mock clip render "takes"
CAPTURE_INTERVAL_S = 2.0
BASE_FPS = 30
CLIPS_HOURS = 24
DAY_START_H, DAY_END_H = 7, 19   # mock daylight: usable frames only between these hours
SIGHTING_DAYS = 14
DAILY_DAYS = 60
SKIPPED_DAILY = 9        # this many days ago the daily video was skipped (too few frames)
KEEP_CLIPS_DAYS = 30
MAX_LIVE_VIEWERS = 3
URL_TTL_S = 12 * 3600
API_VERSION = 1

LABELS_FILE = [
    ("Toyota", "Camry"), ("Toyota", "Corolla"), ("Toyota", "RAV4"), ("Toyota", "Tacoma"), ("Toyota", "4Runner"),
    ("Toyota", "GR86"), ("Honda", "Civic"), ("Honda", "Accord"), ("Honda", "CR-V"), ("Honda", "Odyssey"),
    ("Ford", "F-150"), ("Ford", "Explorer"), ("Ford", "Mustang"), ("Ford", "Bronco"), ("Chevrolet", "Silverado 1500"),
    ("Chevrolet", "Equinox"), ("Chevrolet", "Tahoe"), ("Ram", "1500"), ("Tesla", "Model 3"), ("Tesla", "Model Y"),
    ("Tesla", "Cybertruck"), ("Subaru", "Outback"), ("Subaru", "Forester"), ("Jeep", "Wrangler"),
    ("Jeep", "Grand Cherokee"), ("Nissan", "Rogue"), ("Hyundai", "Tucson"), ("Kia", "Telluride"), ("Mazda", "Mazda3"),
    ("Mazda", "CX-5"), ("BMW", "X5"), ("Porsche", "911"), ("Lamborghini", "Urus"), ("Ferrari", "Roma"),
    "dump truck", "concrete mixer truck", "semi truck", "box truck", "garbage truck", "school bus", "city bus",
    "motorcycle", "excavator",
]
DISCOVERED = [("Rivian", "R1S"), ("Lucid", "Air"), ("Polestar", "2")]
NEVER_SEEN = {"Lamborghini Urus", "Ferrari Roma", "excavator", "Porsche 911"}
CLASS_OF = {"dump truck": "truck", "concrete mixer truck": "truck", "semi truck": "truck", "box truck": "truck",
            "garbage truck": "truck", "school bus": "bus", "city bus": "bus", "motorcycle": "motorcycle",
            "excavator": "truck", "Ford F-150": "truck", "Chevrolet Silverado 1500": "truck", "Ram 1500": "truck",
            "Toyota Tacoma": "truck", "Tesla Cybertruck": "truck"}
COLORS = ["white", "black", "silver", "gray", "blue", "red", "green", "beige"]


def label_name(mk: str, md: str) -> str:
    return md if md.startswith(mk) else f"{mk} {md}"   # same rule as sightings.Labels


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---------------------------------------------------------------- time helpers

def local_zone() -> ZoneInfo:
    tz = os.environ.get("TZ")
    if tz:
        return ZoneInfo(tz)
    real = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in real:
        return ZoneInfo(real.split("zoneinfo/", 1)[1])
    return ZoneInfo("UTC")


class Clock:
    def __init__(self, tz: ZoneInfo, fixed: float | None = None):
        self.tz, self.fixed = tz, fixed

    def now(self) -> float:
        return self.fixed if self.fixed is not None else time.time()

    def dt(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.tz)

    def iso(self, ts: float | None) -> str | None:
        return None if ts is None else self.dt(ts).isoformat(timespec="milliseconds")

    def day(self, ts: float) -> date:
        return self.dt(ts).date()

    def midnight(self, d: date) -> float:
        return datetime.combine(d, dtime.min, tzinfo=self.tz).timestamp()

    def at(self, d: date, hour: float) -> float:
        return self.midnight(d) + hour * 3600

    def window_start(self, ts: float) -> float:
        m = self.midnight(self.day(ts))
        step = INTERVAL_MIN * 60
        return m + (ts - m) // step * step

    def parse(self, s: str) -> float:
        try:
            return float(s)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError as e:
            raise ApiError(400, "invalid_param", f"bad instant {s!r}") from e
        if dt.tzinfo is None:
            raise ApiError(400, "invalid_param", f"instant needs an offset: {s!r}")
        return dt.timestamp()


def daylight(clock: Clock, ts: float) -> bool:
    h = (ts - clock.midnight(clock.day(ts))) / 3600
    return DAY_START_H <= h < DAY_END_H


# ---------------------------------------------------------------- media

def ensure_media() -> dict[str, Path]:
    """Generate the sample media once (ffmpeg). Returns name -> path."""
    MEDIA.mkdir(exist_ok=True)
    ff = shutil.which("ffmpeg")
    specs = {
        "clip.mp4": ["-f", "lavfi", "-i", "testsrc2=size=1440x1080:rate=60", "-t", "10"],
        "daily.mp4": ["-f", "lavfi", "-i", "testsrc2=size=1440x1080:rate=60", "-t", "60"],
        "archive.mp4": ["-f", "lavfi", "-i", "testsrc2=size=1440x1080:rate=60", "-t", "30"],
        "sighting.mp4": ["-f", "lavfi", "-i", "testsrc=size=1280x960:rate=30", "-t", "5"],
    }
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    out = {}
    for name, args in specs.items():
        p = MEDIA / name
        if not p.exists():
            _ff(ff, [*args, *enc, str(p)])
        out[name] = p
    for i in range(6):
        p = MEDIA / f"crop_{i}.jpg"
        if not p.exists():
            _ff(ff, ["-f", "lavfi", "-i", "testsrc2=size=480x320", "-vf", f"hue=h={i * 60}", "-frames:v", "1", str(p)])
        out[p.name] = p
    for name, size in (("frame.jpg", "1920x1440"), ("live_1080.jpg", "1080x810")):
        p = MEDIA / name
        if not p.exists():
            _ff(ff, ["-f", "lavfi", "-i", f"testsrc2=size={size}", "-frames:v", "1", str(p)])
        out[name] = p
    if not (MEDIA / "live_030.jpg").exists():
        _ff(ff, ["-f", "lavfi", "-i", "testsrc2=size=1280x960:rate=10", "-t", "3", "-q:v", "5",
                 str(MEDIA / "live_%03d.jpg")])
    out["live"] = sorted(MEDIA.glob("live_0*.jpg"))
    for name, src in (("poster_clip.jpg", "clip.mp4"), ("poster_daily.jpg", "daily.mp4")):
        p = MEDIA / name
        if not p.exists():
            _ff(ff, ["-ss", "5", "-i", str(MEDIA / src), "-frames:v", "1", "-vf", "scale=640:-2", str(p)])
        out[name] = p
    return out


def _ff(ff, args):
    if not ff:
        raise SystemExit("ffmpeg is needed once to generate tools/mock_media/ (brew install ffmpeg)")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def mvhd_duration(p: Path) -> float | None:
    """Duration from the moov/mvhd box at the head of a +faststart MP4 (what the PC does instead of ffprobe)."""
    data = p.read_bytes()[:256 * 1024]
    i = data.find(b"mvhd")
    if i < 0:
        return None
    ver = data[i + 4]
    if ver == 1:
        scale = int.from_bytes(data[i + 24:i + 28], "big")
        dur = int.from_bytes(data[i + 28:i + 36], "big")
    else:
        scale = int.from_bytes(data[i + 16:i + 20], "big")
        dur = int.from_bytes(data[i + 20:i + 24], "big")
    return round(dur / scale, 3) if scale else None


# ---------------------------------------------------------------- data

class Store:
    def __init__(self, clock: Clock, seed: int, media: dict, sightings_on=True, has_db=True, v1=False,
                 secret=b"mock-secret", dev_token="mock-token"):
        self.clock, self.media = clock, media
        self.sightings_on, self.has_db, self.v1 = sightings_on, has_db, v1
        self.secret = secret
        self.lock = threading.RLock()
        self.rng = random.Random(seed)
        self.labels = self._labels()
        self.sightings: dict[str, dict] = {}
        if has_db:
            self._gen_sightings()
        self.starred: set[tuple[str, str]] = set()      # (kind, id)
        self.hidden: set[str] = set()
        self.corrections: dict[str, tuple[str, float]] = {}
        self.devices: dict[str, dict] = {}
        self.tokens: dict[str, str] = {}                # sha256(token) -> device id
        self.pair_codes: dict[str, float] = {}
        self.pair_attempts: list[float] = []
        self.live_viewers = 0
        self.live_opened = self.live_closed = 0         # for /mock/live (UI tests check disconnects)
        self.live_state = "ok"                           # ok | busy | unreachable | unavailable
        self.offline_until = 0.0                         # /mock/offline: drop /api/ connections until then
        self.rotation = 0
        self.live_jpg_times: list[float] = []
        self._seed_ui_state()
        self.add_device("Mock dev token", "other", token=dev_token, device_id="dev_mockdevicetoken00")

    # -- labels
    def _labels(self) -> list[dict]:
        out = []
        for e in LABELS_FILE:
            if isinstance(e, tuple):
                out.append({"label": label_name(*e), "make": e[0], "model": e[1], "generic": False})
            else:
                out.append({"label": e, "make": None, "model": None, "generic": True})
        return out

    def label_info(self, label: str) -> dict | None:
        for li in self.labels:
            if li["label"] == label:
                return li
        for mk, md in DISCOVERED:
            if label_name(mk, md) == label:
                return {"label": label, "make": mk, "model": md, "generic": False}
        return None

    # -- sightings
    def _gen_sightings(self):
        rng, clock = self.rng, self.clock
        names = [li["label"] for li in self.labels if li["label"] not in NEVER_SEEN]
        rng.shuffle(names)
        weights = [1 / (i + 1) ** 1.15 for i in range(len(names))]
        now = clock.now()
        today = clock.day(now)
        for back in range(SIGHTING_DAYS):
            d = today - timedelta(days=back)
            for _ in range(rng.randint(22, 40)):
                t = clock.at(d, rng.uniform(DAY_START_H, DAY_END_H - 0.1))
                if t > now - 120:
                    continue
                self._add_sighting(t, rng.choices(names, weights)[0], names)
        # a few Gemini discoveries, and a correction target
        for i, (mk, md) in enumerate(DISCOVERED):
            t = clock.at(today - timedelta(days=2 + i * 3), 11.5 + i)
            if t < now - 120:
                self._add_sighting(t, label_name(mk, md), names, force_cloud=True)
        last = max(self.sightings.values(), key=lambda s: s["started"], default=None)
        if last and not self.v1:
            last["cloud_status"] = "pending"

    def _add_sighting(self, t: float, label: str, names: list[str], force_cloud=False):
        rng = self.rng
        sid = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        stationary = rng.random() < 0.05
        dur = rng.uniform(60, 400) if stationary else rng.uniform(1.5, 7)
        conf = round(rng.uniform(0.18, 0.92), 4)
        others = rng.sample([n for n in names if n != label], 2)
        ups = sorted([round(rng.uniform(0.02, conf * 0.8), 4) for _ in others], reverse=True)
        cloud = not self.v1 and (force_cloud or rng.random() < 0.15)
        info = self.label_info(label) or {"make": None, "model": None}
        s = {
            "id": sid, "started": t, "ended": t + dur, "label": label, "make": info["make"], "model": info["model"],
            "yolo_class": CLASS_OF.get(label, "car"), "confidence": conf, "source": "siglip",
            "siglip_label": label, "siglip_conf": conf,
            "runner_ups": [{"label": n, "p": p} for n, p in zip(others, ups)],
            "year_range": None, "color": None, "unsure": conf < 0.30, "stationary": stationary,
            "direction": None if stationary else rng.choice(["LR", "RL"]),
            "cloud_status": None, "track_frames": int(dur * 10), "max_box_px": rng.randint(90, 700),
        }
        if cloud:
            s.update(source="cloud", confidence=round(rng.uniform(0.8, 0.97), 4), cloud_status="done",
                     siglip_label=rng.choice(others) if force_cloud else label,
                     year_range=f"{(y := rng.randint(2012, 2022))}-{y + rng.randint(2, 5)}",
                     color=rng.choice(COLORS))
        elif not self.v1 and s["unsure"]:
            s["cloud_status"] = rng.choice(["capped", "failed", "done", "skipped"])
        d = self.clock.dt(t)
        stem = f"{d:%H%M%S}_{sid[:8]}_{slug(s['siglip_label'])}"
        s["crop"] = f"{d:%Y-%m-%d}/{stem}_crop.jpg"
        s["frame"] = f"{d:%Y-%m-%d}/{stem}_frame.jpg"
        s["clip"] = f"{d:%Y-%m-%d}/{stem}.mp4" if rng.random() > 0.1 else None
        self.sightings[sid] = s

    def _seed_ui_state(self):
        rows = sorted(self.sightings.values(), key=lambda s: s["started"])
        if not rows:
            return
        rng = random.Random(1)
        for s in rng.sample(rows, min(4, len(rows))):
            self.starred.add(("sighting", s["id"]))
        for s in rng.sample(rows, min(2, len(rows))):
            self.hidden.add(s["id"])
        cars = [s for s in rows if s["label"] == "Toyota Camry" and s["id"] not in self.hidden]
        if cars:
            self.corrections[cars[-1]["id"]] = ("Toyota Corolla", cars[-1]["ended"] + 3600)
        clips = self.clips()
        if clips:
            self.starred.add(("clip", clips[len(clips) // 2]["id"]))
        y = self.clock.day(self.clock.now()) - timedelta(days=30)
        self.starred.add(("clip", f"{y:%Y-%m-%d}_1230"))   # starred, long expired -> window highlight
        self.starred.add(("daily", (self.clock.day(self.clock.now()) - timedelta(days=3)).isoformat()))

    # -- effective view
    def effective(self, s: dict) -> dict:
        corr = self.corrections.get(s["id"])
        if corr:
            info = self.label_info(corr[0]) or {}
            return {"label": corr[0], "make": info.get("make"), "model": info.get("model"), "decided_by": "user"}
        return {"label": s["label"], "make": s["make"], "model": s["model"], "decided_by": s["source"]}

    def counted(self, s: dict) -> bool:
        if s["id"] in self.hidden:
            return False
        eff = self.effective(s)
        return bool(eff["label"]) and (not s["unsure"] or eff["decided_by"] in ("cloud", "user"))

    def counted_by_label(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for s in sorted(self.sightings.values(), key=lambda s: (s["started"], s["id"])):
            if self.counted(s):
                out.setdefault(self.effective(s)["label"], []).append(s)
        return out

    def count_between(self, a: float, b: float) -> int | None:
        if not self.has_db:
            return None
        return sum(1 for s in self.sightings.values() if a <= s["started"] < b and self.counted(s))

    # -- timelapse files
    def clips(self) -> list[dict]:
        """Existing clips, newest first."""
        now, clock = self.clock.now(), self.clock
        out = []
        ws = clock.window_start(now) - INTERVAL_MIN * 60
        while ws > now - (CLIPS_HOURS + 1) * 3600:
            we = ws + WINDOW_MIN * 60
            modified = we + DELAY_S + RENDER_S
            if modified <= now and modified > now - CLIPS_HOURS * 3600 and daylight(clock, ws) \
                    and daylight(clock, we - 1):
                out.append({"id": f"{clock.dt(ws):%Y-%m-%d_%H%M}", "start": ws, "end": we, "modified": modified})
            ws -= INTERVAL_MIN * 60
        return out

    def clip(self, cid: str) -> dict | None:
        return next((c for c in self.clips() if c["id"] == cid), None)

    def clip_window(self, cid: str) -> tuple[float, float]:
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})", cid)
        if not m:
            raise ApiError(400, "invalid_param", f"bad clip id {cid!r}")
        d = date.fromisoformat(m.group(1))
        start = self.clock.at(d, int(m.group(2)) + int(m.group(3)) / 60)
        return start, start + WINDOW_MIN * 60

    def dailies(self) -> list[date]:
        today = self.clock.day(self.clock.now())
        rendered = self.clock.now() >= self.clock.at(today, 0 + 5 / 60 + 0.05)   # run_at 00:05 + render time
        out = []
        for back in range(1, DAILY_DAYS + 1):
            if back == 1 and not rendered:
                continue
            if back != SKIPPED_DAILY:
                out.append(today - timedelta(days=back))
        return out

    def archive_parts(self) -> list[dict]:
        now = self.clock.now()
        return [{"part": 3, "current": True, "size": 1_234_567_890, "modified": now - 300},
                {"part": 2, "current": False, "size": None, "modified": now - 9 * 86400},
                {"part": 1, "current": False, "size": None, "modified": now - 30 * 86400}]

    # -- devices
    def add_device(self, name: str, platform: str, token: str | None = None, device_id: str | None = None):
        token = token or base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        did = device_id or "dev_" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz234567") for _ in range(16))
        self.devices[did] = {"id": did, "name": name, "platform": platform, "created": self.clock.now(),
                             "last_seen": None, "apns_token": None, "environment": None,
                             "prefs": {"new_catch": True, "rare": False, "discovered": True, "service_alerts": True},
                             "revoked": False}
        self.tokens[hashlib.sha256(token.encode()).hexdigest()] = did
        return token, self.devices[did]

    def new_pair_code(self) -> str:
        alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        self.pair_codes[code] = self.clock.now() + 600
        return f"{code[:4]}-{code[4:]}"

    # -- signing
    def sign(self, path: str, device_id: str) -> str:
        exp = int(self.clock.now() + URL_TTL_S)
        mac = hmac.new(self.secret, f"{device_id}|{path}|{exp}".encode(), hashlib.sha256).digest()
        sig = base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
        return f"{path}?d={device_id}&exp={exp}&sig={sig}"

    def check_sig(self, path: str, q: dict) -> str:
        try:
            d, exp, sig = q["d"][0], int(q["exp"][0]), q["sig"][0]
        except (KeyError, ValueError, IndexError):
            raise ApiError(401, "unauthorized", "missing token or signature")
        dev = self.devices.get(d)
        mac = hmac.new(self.secret, f"{d}|{path}|{exp}".encode(), hashlib.sha256).digest()
        good = base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
        if not dev or dev["revoked"] or not hmac.compare_digest(good, sig):
            raise ApiError(401, "unauthorized", "bad signature")
        if exp < time.time():
            raise ApiError(403, "expired", "signed URL expired")
        return d


# ---------------------------------------------------------------- JSON views

class Views:
    def __init__(self, store: Store):
        self.s = store
        self.c = store.clock

    def media(self, root: str, rel: str, dev: str) -> str:
        return self.s.sign(f"/media/{root}/{quote(rel)}", dev)

    def sighting(self, s: dict, dev: str) -> dict:
        st, c = self.s, self.c
        eff = st.effective(s)
        corr = st.corrections.get(s["id"])
        return {
            "id": s["id"], "kind": "vehicle", "started_at": c.iso(s["started"]), "ended_at": c.iso(s["ended"]),
            "day": c.day(s["started"]).isoformat(), "yolo_class": s["yolo_class"],
            "label": eff["label"], "make": eff["make"], "model": eff["model"], "confidence": s["confidence"],
            "decided_by": eff["decided_by"],
            "machine": {"label": s["label"], "make": s["make"], "model": s["model"], "confidence": s["confidence"],
                        "source": s["source"]},
            "siglip": {"label": s["siglip_label"], "confidence": s["siglip_conf"]},
            "runner_ups": s["runner_ups"], "year_range": s["year_range"], "color": s["color"],
            "unsure": s["unsure"], "stationary": s["stationary"], "direction": s["direction"],
            "cloud_status": s["cloud_status"], "starred": ("sighting", s["id"]) in st.starred,
            "hidden": s["id"] in st.hidden,
            "correction": {"label": corr[0], "at": c.iso(corr[1])} if corr else None,
            "track_frames": s["track_frames"], "max_box_px": s["max_box_px"],
            "media": {"crop": self.media("sightings", s["crop"], dev),
                      "frame": self.media("sightings", s["frame"], dev),
                      "clip": self.media("sightings", s["clip"], dev) if s["clip"] else None},
        }

    def clip(self, cl: dict, dev: str) -> dict:
        p = self.s.media["clip.mp4"]
        return {
            "id": cl["id"], "day": self.c.day(cl["start"]).isoformat(), "window_start": self.c.iso(cl["start"]),
            "window_end": self.c.iso(cl["end"]), "exact_window": True, "duration_s": mvhd_duration(p),
            "size_bytes": p.stat().st_size, "modified_at": self.c.iso(cl["modified"]),
            "expires_after": self.c.iso(cl["modified"] + CLIPS_HOURS * 3600),
            "sightings_count": self.s.count_between(cl["start"], cl["end"]),
            "starred": ("clip", cl["id"]) in self.s.starred,
            "media": {"video": self.media("clips", f"campi_{cl['id']}.mp4", dev),
                      "poster": self.s.sign(f"/media/posters/clips/{cl['id']}.jpg", dev)},
        }

    def daily(self, d: date, dev: str) -> dict:
        p = self.s.media["daily.mp4"]
        m = self.c.midnight(d)
        return {
            "day": d.isoformat(), "duration_s": mvhd_duration(p), "size_bytes": p.stat().st_size,
            "modified_at": self.c.iso(self.c.midnight(d + timedelta(days=1)) + 400),
            "sightings_count": self.s.count_between(m, self.c.midnight(d + timedelta(days=1))),
            "starred": ("daily", d.isoformat()) in self.s.starred,
            "media": {"video": self.media("daily", f"campi_daily_{d.isoformat()}.mp4", dev),
                      "poster": self.s.sign(f"/media/posters/daily/{d.isoformat()}.jpg", dev)},
        }

    def status(self, dev: str) -> dict:
        st, c = self.s, self.c
        now = c.now()
        clips = st.clips()
        dark = not daylight(c, now)
        sightings_rows = list(st.sightings.values())
        today = c.day(now)
        nxt = c.window_start(now) + INTERVAL_MIN * 60
        reserved = not st.v1
        if not st.sightings_on:
            sstate, phase = "disabled", None
        else:
            sstate, phase = "running", ("paused_dark" if dark else "running")
        return {
            "api_version": API_VERSION, "server_name": "CAMPI-PC", "server_time": c.iso(now),
            "service": {"state": "running", "pid": 10432, "started_at": c.iso(now - 3 * 86400 - 1234),
                        "heartbeat_at": c.iso(now - 2), "disabled": False},
            "capture": {"connected": True, "host": "192.168.1.50", "last_frame_at": c.iso(now - 1.4),
                        "saved": 128_734, "rejected": 12, "reconnects": 3, "restarts": 0, "last_error": None},
            "clips": {
                "last": ({"status": "ok", "finished_at": c.iso(clips[0]["modified"]), "clip_id": clips[0]["id"],
                          "day": None, "detail": None} if clips else
                         {"status": "skipped", "finished_at": c.iso(nxt - INTERVAL_MIN * 60 + 75), "clip_id": None,
                          "day": None, "detail": "0 usable of 300 captured (need 150; night threshold 35)"}),
                "next_at": c.iso(nxt), "running": False,
                "queue": {"length": 0, "oldest_window_start": None, "deferred": False, "deferred_reason": None}
                if reserved else None,
            },
            "daily": {"last": {"status": "ok", "finished_at": c.iso(c.at(today, 0.2)), "clip_id": None,
                               "day": (today - timedelta(days=1)).isoformat(), "detail": None}, "running": False},
            "sightings": {
                "enabled": st.sightings_on, "state": sstate, "phase": phase,
                "restarts": 1 if st.sightings_on else None, "last_error": None, "next_retry_at": None,
                "has_history": st.has_db,
                "today": sum(1 for s in sightings_rows if c.day(s["started"]) == today),
                "total": len(sightings_rows),
                "last_sighting_at": c.iso(max((s["started"] for s in sightings_rows), default=None)),
                "backend": "openvino" if reserved and st.sightings_on else None,
                "device": "Intel(R) UHD Graphics 770 (iGPU)" if reserved and st.sightings_on else None,
                "cpu_fallback": False if reserved and st.sightings_on else None,
                "classify_queue": 0 if reserved and st.sightings_on else None,
                "cloud": {"enabled": True, "calls_today": 37, "cap": 400} if reserved and st.sightings_on else None,
            },
            "gaming": {"mode": "auto", "active": False, "exe": None, "since": None, "detection": "counters",
                       "renders_deferred": False} if reserved else None,
            "disk": {"free_gb": 412.7, "drive": "C:"},
            "latest_clip_id": clips[0]["id"] if clips else None,
            "live": {"available": st.live_state != "unavailable", "mjpeg": st.sign("/live.mjpg", dev),
                     "snapshot": st.sign("/live.jpg", dev), "rotation": st.rotation, "max_viewers": MAX_LIVE_VIEWERS},
        }

    def collection(self, dev: str) -> dict:
        st = self.s
        by = st.counted_by_label()
        items = []
        seen = set()

        def item(label, make, model, generic, origin):
            rows = by.get(label, [])
            best = max(rows, key=lambda s: (s["confidence"] or 0, s["started"]), default=None)
            seen.add(label)
            return {"label": label, "make": make, "model": model, "generic": generic, "origin": origin,
                    "count": len(rows),
                    "first_seen_at": self.c.iso(rows[0]["started"]) if rows else None,
                    "last_seen_at": self.c.iso(rows[-1]["started"]) if rows else None,
                    "tier": tier(len(rows)),
                    "cover": {"sighting_id": best["id"], "crop": self.media("sightings", best["crop"], dev)}
                    if best else None}

        for li in st.labels:
            items.append(item(li["label"], li["make"], li["model"], li["generic"], "labels_file"))
        disc = []
        for mk, md in DISCOVERED:
            name = label_name(mk, md)
            if any(s["label"] == name for s in st.sightings.values()):
                disc.append(item(name, mk, md, False, "discovered"))
        items += sorted(disc, key=lambda i: i["first_seen_at"] or "")
        items += [item(lab, None, None, False, "other") for lab in sorted(by) if lab not in seen]
        return {"total": len(items), "caught": sum(1 for i in items if i["count"] > 0), "tiers": TIERS,
                "items": items}

    def highlights_all(self, dev: str) -> list[dict]:
        st, c = self.s, self.c
        out: dict[str, dict] = {}

        def add(hid, typ, at, day, **kw):
            h = out.setdefault(hid, {"id": hid, "types": [], "at": at, "day": day, "sighting": None, "clip": None,
                                     "daily": None, "window": None})
            if typ not in h["types"]:
                h["types"].append(typ)
            for k, v in kw.items():
                if v is not None:
                    h[k] = v

        def sighting_item(s, typ):
            add(f"sighting:{s['id']}", typ, s["started"], c.day(s["started"]).isoformat(), sighting=self.sighting(s, dev))

        def window_item(cid, typ):
            a, b = st.clip_window(cid)
            cl = st.clip(cid)
            add(f"window:{cid}", typ, a, c.day(a).isoformat(), clip=self.clip(cl, dev) if cl else None,
                window={"start": c.iso(a), "end": c.iso(b), "sightings_count": st.count_between(a, b) or 0})

        by = st.counted_by_label()
        for rows in by.values():
            sighting_item(rows[0], "new_catch")
            if len(rows) <= 3:
                for s in rows:
                    sighting_item(s, "rare")
        windows: dict[float, int] = {}
        for rows in by.values():
            for s in rows:
                w = c.window_start(s["started"])
                windows[w] = windows.get(w, 0) + 1
        per_day: dict[date, list] = {}
        for w, n in windows.items():
            if n >= 3:
                per_day.setdefault(c.day(w), []).append((n, w))
        busiest = set()
        for lst in per_day.values():
            for n, w in sorted(lst, reverse=True)[:3]:
                cid = f"{c.dt(w):%Y-%m-%d_%H%M}"
                busiest.add(cid)
                window_item(cid, "busiest")
        for d in st.dailies():
            add(f"daily:{d.isoformat()}", "daily", c.midnight(d + timedelta(days=1)), d.isoformat(),
                daily=self.daily(d, dev))
        for kind, ident in st.starred:
            if kind == "sighting" and ident in st.sightings:
                sighting_item(st.sightings[ident], "starred")
            elif kind == "clip":
                cl = st.clip(ident)
                if cl and ident not in busiest:
                    add(f"clip:{ident}", "starred", cl["start"], c.day(cl["start"]).isoformat(), clip=self.clip(cl, dev))
                else:
                    window_item(ident, "starred")
            elif kind == "daily":
                d = date.fromisoformat(ident)
                if d in st.dailies():
                    add(f"daily:{ident}", "starred", c.midnight(d + timedelta(days=1)), ident, daily=self.daily(d, dev))
        items = list(out.values())
        for h in items:
            h["types"] = [t for t in ("new_catch", "rare", "busiest", "daily", "starred") if t in h["types"]]
        return items

    def seek(self, ts: float, dev: str) -> dict:
        st, c = self.s, self.c
        now = c.now()
        if ts > now + 1:
            raise ApiError(400, "invalid_param", "ts is in the future")
        base = {"ts": c.iso(ts), "target": "none", "clip_id": None, "day": None, "video": None, "offset_s": None,
                "approximate": False, "reason": None}
        day = c.day(ts)
        if day >= c.day(now) - timedelta(days=DAILY_DAYS) and not daylight(c, ts) and \
                not (daylight(c, ts - WINDOW_MIN * 60) or daylight(c, ts + WINDOW_MIN * 60)):
            return {**base, "reason": "no_frames"}
        for cl in st.clips():
            if cl["start"] <= ts < cl["end"]:
                k = int((ts - cl["start"]) // CAPTURE_INTERVAL_S)
                dur = mvhd_duration(st.media["clip.mp4"]) or 10.0
                off = min(k / BASE_FPS, dur - 0.05)
                return {**base, "target": "clip", "clip_id": cl["id"], "offset_s": round(off, 3),
                        "video": self.clip(cl, dev)["media"]["video"]}
        if day in st.dailies():
            dur = mvhd_duration(st.media["daily.mp4"]) or 60.0
            usable = (DAY_END_H - DAY_START_H) * 60
            before = min(max((ts - c.at(day, DAY_START_H)) / 60, 0), usable)
            return {**base, "target": "daily", "day": day.isoformat(), "approximate": True,
                    "offset_s": round(min(dur * before / usable, dur - 0.05), 3),
                    "video": self.daily(day, dev)["media"]["video"]}
        if ts > now - (WINDOW_MIN * 60 + DELAY_S + RENDER_S + 9 * 60):
            return {**base, "reason": "pending_render"}
        if ts < now - CLIPS_HOURS * 3600:
            return {**base, "reason": "expired"}
        return {**base, "reason": "not_rendered"}


TIERS = [{"tier": "rare", "min": 1, "max": 3}, {"tier": "uncommon", "min": 4, "max": 20},
         {"tier": "common", "min": 21, "max": None}]


def tier(n: int) -> str:
    if n == 0:
        return "uncaught"
    for t in TIERS:
        if n >= t["min"] and (t["max"] is None or n <= t["max"]):
            return t["tier"]
    return "common"


def push_payload(store: Store, kind: str) -> dict:
    if kind == "service":
        return {"aps": {"alert": {"title": "Campi: stream lost", "body": "No frame saved for 10 minutes."},
                        "sound": "default", "mutable-content": 1, "thread-id": "service", "category": "SERVICE"},
                "campi": {"type": "service", "sighting_id": None, "label": None, "day": None, "crop": None,
                          "alert": "capture_disconnected"}}
    rows = sorted(store.sightings.values(), key=lambda s: s["started"], reverse=True)
    s = next(r for r in rows if store.counted(r))
    label = store.effective(s)["label"]
    title = {"new_catch": f"New catch: {label}", "rare": f"Rare: {label}", "discovered": f"Discovered: {label}"}[kind]
    when = store.clock.dt(s["started"]).strftime("%-I:%M %p")
    return {"aps": {"alert": {"title": title, "body": f"{when} · {s['yolo_class']}"}, "sound": "default",
                    "mutable-content": 1, "thread-id": "sightings", "category": "SIGHTING"},
            "campi": {"type": kind, "sighting_id": s["id"], "label": label,
                      "day": store.clock.day(s["started"]).isoformat(), "crop": f"/media/sightings/{quote(s['crop'])}",
                      "alert": None}}


# ---------------------------------------------------------------- paging

def encode_cursor(key) -> str:
    return base64.urlsafe_b64encode(json.dumps(key).encode()).rstrip(b"=").decode()


def decode_cursor(cur: str):
    try:
        return json.loads(base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4)))
    except Exception as e:
        raise ApiError(400, "invalid_param", "bad cursor") from e


def page(items: list, key, q: dict) -> dict:
    """items sorted newest first by key(item) (a JSON-able list); keyset paging."""
    limit = int_param(q, "limit", 50, 1, 200)
    cur = q.get("cursor", [None])[0]
    if cur:
        after = decode_cursor(cur)
        items = [i for i in items if key(i) < after]
    chunk = items[:limit]
    return {"chunk": chunk, "next_cursor": encode_cursor(key(chunk[-1])) if len(items) > limit else None}


def int_param(q, name, default, lo, hi):
    v = q.get(name, [None])[0]
    if v is None:
        return default
    try:
        n = int(v)
    except ValueError as e:
        raise ApiError(400, "invalid_param", f"{name} must be an integer") from e
    if not lo <= n <= hi:
        raise ApiError(400, "invalid_param", f"{name} must be {lo}-{hi}")
    return n


def bool_param(q, name) -> bool:
    v = q.get(name, ["false"])[0].lower()
    if v not in ("true", "false", "1", "0"):
        raise ApiError(400, "invalid_param", f"{name} must be true or false")
    return v in ("true", "1")


def date_param(q, name) -> date | None:
    v = q.get(name, [None])[0]
    if v is None:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError as e:
        raise ApiError(400, "invalid_param", f"{name} must be YYYY-MM-DD") from e


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CampiMock/1"
    store: Store
    views: Views
    latency = 0.0
    quiet = False
    bundle_id = "com.braxtonmills.campi"

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    # -- plumbing
    def send_json(self, status: int, obj, extra_headers=()):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error_json(self, e: ApiError):
        self.send_json(e.status, {"error": {"code": e.code, "message": e.message}})

    def body_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            obj = json.loads(raw or b"{}")
        except ValueError as e:
            raise ApiError(400, "invalid_param", "body is not JSON") from e
        if not isinstance(obj, dict):
            raise ApiError(400, "invalid_param", "body must be an object")
        return obj

    def auth(self, path: str, q: dict, allow_sig: bool) -> str:
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            did = self.store.tokens.get(hashlib.sha256(h[7:].strip().encode()).hexdigest())
            dev = self.store.devices.get(did) if did else None
            if not dev or dev["revoked"]:
                raise ApiError(401, "unauthorized", "unknown or revoked token")
            dev["last_seen"] = self.store.clock.now()
            return did
        if allow_sig:
            return self.store.check_sig(path, q)
        raise ApiError(401, "unauthorized", "missing bearer token")

    def do_GET(self):
        self.dispatch()

    def do_HEAD(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def dispatch(self):
        if self.latency:
            time.sleep(self.latency)
        u = urlsplit(self.path)
        path, q = u.path, parse_qs(u.query)
        try:
            if path.startswith(("/api/", "/media/", "/live")) and time.time() < self.store.offline_until:
                self.close_connection = True   # no answer at all, like the PC behind a Tailscale that's off
                return
            if path.startswith("/media/"):
                with self.store.lock:
                    self.auth(path, q, allow_sig=True)
                    f, ctype, cache = self.resolve_media(path)
                return self.send_file(f, ctype, cache)   # streamed without holding the lock
            with self.store.lock:
                if path in ("/live.mjpg", "/live.jpg"):
                    self.auth(path, q, allow_sig=True)
                    if path == "/live.jpg":
                        return self.serve_live_jpg()
            if path == "/live.mjpg":
                return self.serve_mjpeg(q)
            if path == "/mock/push" and self.command == "POST":   # mock-only: simulated APNs push to the simulator
                return self.send_json(200, self.simulator_push())
            if path == "/mock/offline" and self.command == "POST":   # mock-only: act unreachable for N seconds
                secs = float(self.body_json().get("seconds", 60))
                self.store.offline_until = time.time() + secs
                return self.send_json(200, {"offline_for_s": secs})
            with self.store.lock:
                if path == "/mock/pair-code" and self.command == "POST":   # mock-only: fresh code for UI tests
                    self.store.pair_attempts.clear()   # each test pairs once; don't trip the 10-per-10-min limit
                    return self.send_json(201, {"code": self.store.new_pair_code()})
                if path == "/mock/live":   # mock-only: live viewer counters; POST {"state", "rotation"} changes them
                    if self.command == "POST":
                        b = self.body_json()
                        if b.get("state") not in (None, "ok", "busy", "unreachable", "unavailable"):
                            raise ApiError(400, "invalid_param", "state: ok | busy | unreachable | unavailable")
                        self.store.live_state = b.get("state") or self.store.live_state
                        self.store.rotation = int(b.get("rotation", self.store.rotation))
                    st = self.store
                    return self.send_json(200, {"state": st.live_state, "rotation": st.rotation,
                                                "viewers": st.live_viewers, "opened": st.live_opened,
                                                "closed": st.live_closed})
                if path == "/api/pair" and self.command == "POST":
                    return self.send_json(201, self.pair())
                if not path.startswith("/api/"):
                    raise ApiError(404, "not_found", f"no route {path}")
                dev = self.auth(path, q, allow_sig=False)
                status, obj = self.route(self.command, path, q, dev)
                if status == 204:
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_json(status, obj)
        except ApiError as e:
            self.send_error_json(e)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- API routes
    def route(self, method: str, path: str, q: dict, dev: str):
        st, v = self.store, self.views
        parts = path.strip("/").split("/")[1:]   # after "api"
        if not parts or not parts[0]:
            raise ApiError(404, "not_found", f"no route {path}")
        if method == "GET" and parts == ["status"]:
            return 200, v.status(dev)
        if parts[:2] == ["devices", "me"]:
            d = st.devices[dev]
            if method == "GET" and len(parts) == 2:
                return 200, self.device_json(d)
            if method == "DELETE" and len(parts) == 2:
                d["revoked"] = True
                return 204, None
            if method == "PUT" and parts[2:] == ["push"]:
                b = self.body_json()
                prefs = b.get("prefs")
                if b.get("environment") not in ("sandbox", "production") or not isinstance(prefs, dict) or \
                        set(prefs) != {"new_catch", "rare", "discovered", "service_alerts"} or \
                        not all(isinstance(x, bool) for x in prefs.values()) or \
                        not (b.get("apns_token") is None or isinstance(b.get("apns_token"), str)):
                    raise ApiError(400, "invalid_param", "need apns_token, environment and all four prefs")
                d.update(apns_token=b["apns_token"], environment=b["environment"], prefs=prefs)
                return 200, self.device_json(d)
        if parts[0] == "sightings":
            if not st.has_db and len(parts) > 1:
                raise ApiError(404, "not_found", "no sightings database")
            if method == "GET" and len(parts) == 1:
                return 200, self.list_sightings(q, dev)
            s = st.sightings.get(parts[1]) if len(parts) > 1 else None
            if s is None:
                raise ApiError(404, "not_found", f"no sighting {parts[1] if len(parts) > 1 else ''}")
            if method == "GET" and len(parts) == 2:
                return 200, v.sighting(s, dev)
            if method == "POST" and len(parts) == 3:
                b = self.body_json()
                if parts[2] == "star":
                    flag = self.flag(b, "starred")
                    (st.starred.add if flag else st.starred.discard)(("sighting", s["id"]))
                elif parts[2] == "hide":
                    flag = self.flag(b, "hidden")
                    (st.hidden.add if flag else st.hidden.discard)(s["id"])
                elif parts[2] == "label":
                    if "label" not in b or not (b["label"] is None or isinstance(b["label"], str)):
                        raise ApiError(400, "invalid_param", "body needs label (string or null)")
                    if b["label"] is None:
                        st.corrections.pop(s["id"], None)
                    else:
                        known = {i["label"] for i in v.collection(dev)["items"]}
                        if b["label"] not in known:
                            raise ApiError(422, "unknown_label", f"{b['label']!r} is not in the collection")
                        st.corrections[s["id"]] = (b["label"], st.clock.now())
                else:
                    raise ApiError(404, "not_found", f"no route {path}")
                return 200, v.sighting(s, dev)
        if method == "GET" and parts == ["collection"]:
            return 200, v.collection(dev)
        if method == "GET" and parts == ["highlights"]:
            return 200, self.list_highlights(q, dev)
        if parts[0] == "clips":
            if method == "GET" and len(parts) == 1:
                clips = st.clips()
                return 200, {"latest_id": clips[0]["id"] if clips else None, "retention_hours": CLIPS_HOURS,
                             "window_min": WINDOW_MIN, "items": [v.clip(c, dev) for c in clips]}
            cl = st.clip(parts[1]) if len(parts) > 1 else None
            if cl is None:
                raise ApiError(404, "not_found", f"no clip {parts[1] if len(parts) > 1 else ''}")
            if method == "GET" and len(parts) == 2:
                return 200, v.clip(cl, dev)
            if method == "POST" and parts[2:] == ["star"]:
                flag = self.flag(self.body_json(), "starred")
                (st.starred.add if flag else st.starred.discard)(("clip", cl["id"]))
                return 200, v.clip(cl, dev)
        if parts[0] == "daily":
            days = st.dailies()
            if method == "GET" and len(parts) == 1:
                pg = page(days, lambda d: [d.isoformat()], q)
                return 200, {"items": [v.daily(d, dev) for d in pg["chunk"]], "next_cursor": pg["next_cursor"],
                             "total": len(days)}
            try:
                d = date.fromisoformat(parts[1])
            except (ValueError, IndexError):
                raise ApiError(404, "not_found", "no such daily video")
            if d not in days:
                raise ApiError(404, "not_found", f"no daily video for {d}")
            if method == "GET" and len(parts) == 2:
                return 200, v.daily(d, dev)
            if method == "POST" and parts[2:] == ["star"]:
                flag = self.flag(self.body_json(), "starred")
                (st.starred.add if flag else st.starred.discard)(("daily", d.isoformat()))
                return 200, v.daily(d, dev)
        if method == "GET" and parts == ["archive"]:
            p = st.media["archive.mp4"]
            items = []
            for a in st.archive_parts():
                items.append({"part": a["part"], "size_bytes": a["size"] or p.stat().st_size,
                              "modified_at": st.clock.iso(a["modified"]),
                              "duration_s": None if a["current"] else mvhd_duration(p), "current": a["current"],
                              "media": {"video": None if a["current"] else
                                        v.media("archive", f"campi_archive_{a['part']:03d}.mp4", dev)}})
            return 200, {"enabled": True, "items": items}
        if method == "GET" and parts == ["seek"]:
            ts = q.get("ts", [None])[0]
            if ts is None:
                raise ApiError(400, "invalid_param", "ts is required")
            return 200, v.seek(st.clock.parse(ts), dev)
        raise ApiError(404, "not_found", f"no route {method} {path}")

    @staticmethod
    def flag(b: dict, key: str) -> bool:
        if not isinstance(b.get(key), bool):
            raise ApiError(400, "invalid_param", f"body needs {key}: true|false")
        return b[key]

    def device_json(self, d: dict) -> dict:
        c = self.store.clock
        return {"id": d["id"], "name": d["name"], "platform": d["platform"], "created_at": c.iso(d["created"]),
                "last_seen_at": c.iso(d["last_seen"]),
                "push": {"enabled": d["apns_token"] is not None, "environment": d["environment"],
                         "prefs": d["prefs"]}}

    def pair(self) -> dict:
        st = self.store
        now = st.clock.now()
        st.pair_attempts = [t for t in st.pair_attempts if t > time.time() - 600] + [time.time()]
        if len(st.pair_attempts) > 10:
            raise ApiError(429, "rate_limited", "too many pairing attempts; wait a few minutes")
        b = self.body_json()
        code = re.sub(r"[\s-]", "", str(b.get("code", ""))).upper()
        name, platform = b.get("device_name"), b.get("platform", "other")
        if not isinstance(name, str) or not name.strip() or platform not in ("ios", "desktop", "other"):
            raise ApiError(400, "invalid_param", "need code, device_name and platform")
        exp = st.pair_codes.pop(code, None)
        if exp is None or exp < now:
            raise ApiError(401, "invalid_code", "pairing code is wrong, used or expired")
        token, dev = st.add_device(name.strip(), platform)
        print(f"paired device {dev['id']} ({name})", file=sys.stderr)
        return {"token": token, "device": self.device_json(dev), "server_name": "CAMPI-PC", "api_version": API_VERSION}

    def list_sightings(self, q: dict, dev: str) -> dict:
        st, c = self.store, self.store.clock
        frm, to = date_param(q, "from"), date_param(q, "to")
        classes, makes = set(q.get("class", [])), set(q.get("make", []))
        label = q.get("label", [None])[0]
        decided = set(q.get("decided_by", []))
        if decided - {"siglip", "cloud", "user"}:
            raise ApiError(400, "invalid_param", "decided_by must be siglip, cloud or user")
        starred, hide_unsure = bool_param(q, "starred"), bool_param(q, "hide_unsure")
        hide_stat, incl_hidden = bool_param(q, "hide_stationary"), bool_param(q, "include_hidden")
        rows = []
        for s in st.sightings.values():
            eff = st.effective(s)
            d = c.day(s["started"])
            if (frm and d < frm) or (to and d > to) or (classes and s["yolo_class"] not in classes) \
                    or (makes and eff["make"] not in makes) or (label and eff["label"] != label) \
                    or (decided and eff["decided_by"] not in decided) \
                    or (starred and ("sighting", s["id"]) not in st.starred) \
                    or (hide_unsure and s["unsure"] and eff["decided_by"] == "siglip") \
                    or (hide_stat and s["stationary"]) or (not incl_hidden and s["id"] in st.hidden):
                continue
            rows.append(s)
        rows.sort(key=lambda s: (s["started"], s["id"]), reverse=True)
        pg = page(rows, lambda s: [s["started"], s["id"]], q)
        return {"items": [self.views.sighting(s, dev) for s in pg["chunk"]], "next_cursor": pg["next_cursor"],
                "total": len(rows)}

    def list_highlights(self, q: dict, dev: str) -> dict:
        types = set(q.get("type", []))
        if types - {"new_catch", "rare", "busiest", "daily", "starred"}:
            raise ApiError(400, "invalid_param", "unknown highlight type")
        frm, to = date_param(q, "from"), date_param(q, "to")
        items = [h for h in self.views.highlights_all(dev)
                 if (not types or types & set(h["types"]))
                 and (not frm or h["day"] >= frm.isoformat()) and (not to or h["day"] <= to.isoformat())]
        items.sort(key=lambda h: (h["at"], h["id"]), reverse=True)
        pg = page(items, lambda h: [h["at"], h["id"]], q)
        c = self.store.clock
        return {"items": [{**h, "at": c.iso(h["at"])} for h in pg["chunk"]], "next_cursor": pg["next_cursor"],
                "total": len(items)}

    # -- media
    def resolve_media(self, path: str) -> tuple[Path, str, str]:
        st = self.store
        rel = unquote(path[len("/media/"):])
        if ".." in rel.split("/") or "\\" in rel:
            raise ApiError(404, "not_found", "outside media roots")
        root, _, name = rel.partition("/")
        f = None
        if root == "clips":
            m = re.fullmatch(r"campi_(\d{4}-\d{2}-\d{2}_\d{4})\.mp4", name)
            f = st.media["clip.mp4"] if m and st.clip(m.group(1)) else None
        elif root == "daily":
            m = re.fullmatch(r"campi_daily_(\d{4}-\d{2}-\d{2})\.mp4", name)
            f = st.media["daily.mp4"] if m and date.fromisoformat(m.group(1)) in st.dailies() else None
        elif root == "archive":
            m = re.fullmatch(r"campi_archive_(\d{3})\.mp4", name)
            ok = m and any(a["part"] == int(m.group(1)) and not a["current"] for a in st.archive_parts())
            f = st.media["archive.mp4"] if ok else None
        elif root == "sightings":
            for s in st.sightings.values():
                if name == s["crop"]:
                    f = st.media[f"crop_{int(s['id'][:8], 16) % 6}.jpg"]
                elif name == s["frame"]:
                    f = st.media["frame.jpg"]
                elif s["clip"] and name == s["clip"]:
                    f = st.media["sighting.mp4"]
                if f:
                    break
        elif root == "posters":
            kind, _, ident = name.partition("/")
            ident = ident.removesuffix(".jpg")
            if kind == "clips" and st.clip(ident):
                f = st.media["poster_clip.jpg"]
            elif kind == "daily" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ident) and \
                    date.fromisoformat(ident) in st.dailies():
                f = st.media["poster_daily.jpg"]
        if f is None:
            raise ApiError(404, "not_found", f"no media {rel}")
        ctype = "video/mp4" if f.suffix == ".mp4" else "image/jpeg"
        cache = "private, max-age=86400" if root == "sightings" and ctype == "image/jpeg" else "private, max-age=300"
        return f, ctype, cache

    def send_file(self, f: Path, ctype: str, cache: str):
        stt = f.stat()
        size = stt.st_size
        etag = f'"{size}-{stt.st_mtime_ns}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and (self.headers.get("If-Range") in (None, etag)):
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if m and "," not in rng:
                a, b = m.groups()
                if a == "" and b == "":
                    m = None
                elif a == "":
                    start, end = max(0, size - int(b)), size - 1
                else:
                    start, end = int(a), min(int(b), size - 1) if b else size - 1
                if m and (start >= size or start > end):
                    self.send_response(416)
                    body = json.dumps({"error": {"code": "invalid_range", "message": rng}}).encode()
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if m:
                    status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", self.date_time_string(stt.st_mtime))
        self.send_header("Cache-Control", cache)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(f, "rb") as fh:   # opened per response and closed when it ends (FILE_SHARE_DELETE on the PC)
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def serve_live_jpg(self):
        st = self.store
        now = time.time()
        st.live_jpg_times = [t for t in st.live_jpg_times if t > now - 1] + [now]
        if len(st.live_jpg_times) > 4:
            raise ApiError(429, "rate_limited", "at most 4 /live.jpg per second")
        if st.live_state == "unavailable":
            raise ApiError(404, "not_found", "no frame in the last 10 minutes")
        f = st.media["live"][int(now / CAPTURE_INTERVAL_S) % len(st.media["live"])]
        if "w=" in (urlsplit(self.path).query or ""):
            f = st.media["live_1080.jpg"]
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Time", st.clock.iso(st.clock.now() - 1.2))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def simulator_push(self) -> dict:
        """`xcrun simctl push booted` with a contract payload (§9.3), like the PC's sender would send via APNs."""
        kind = self.body_json().get("type", "new_catch")
        if kind not in ("new_catch", "rare", "discovered", "service"):
            raise ApiError(400, "invalid_param", "type: new_catch | rare | discovered | service")
        with self.store.lock:
            payload = push_payload(self.store, kind)
        payload["Simulator Target Bundle"] = self.bundle_id
        f = MEDIA / "push.apns"   # git-ignored
        f.write_text(json.dumps(payload))
        r = subprocess.run(["xcrun", "simctl", "push", "booted", self.bundle_id, str(f)],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ApiError(500, "internal", f"simctl push failed: {r.stderr.strip()}")
        return {"sent": payload["campi"]}

    def serve_mjpeg(self, q: dict):
        st = self.store
        max_fps = int_param(q, "max_fps", 30, 1, 30)
        if st.live_state in ("unreachable", "unavailable"):
            time.sleep(1)   # [stream] timeout_s, shortened
            raise ApiError(502, "pi_unreachable", "the Pi didn't answer")
        with st.lock:
            if st.live_viewers >= MAX_LIVE_VIEWERS or st.live_state == "busy":
                raise ApiError(503, "live_busy", f"{MAX_LIVE_VIEWERS} viewers already watching")
            st.live_viewers += 1
            st.live_opened += 1
        print(f"live viewer connected ({st.live_viewers}), max_fps={max_fps}", file=sys.stderr)
        frames = [p.read_bytes() for p in st.media["live"]]
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=campiframe")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            i = 0
            period = 1 / min(max_fps, 10)   # mock source is 10 fps
            while True:
                data = frames[i % len(frames)]
                self.wfile.write(b"--campiframe\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                self.wfile.flush()
                i += 1
                time.sleep(period)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with st.lock:
                st.live_viewers -= 1
                st.live_closed += 1
            print(f"live viewer disconnected ({st.live_viewers} left); upstream closed", file=sys.stderr)


# ---------------------------------------------------------------- fixtures

FIXED_NOW = datetime(2026, 10, 3, 14, 30, 0, tzinfo=ZoneInfo("America/Chicago")).timestamp()


def write_fixtures():
    media = ensure_media()
    tz = ZoneInfo("America/Chicago")
    clock = Clock(tz, FIXED_NOW)
    store = Store(clock, seed=7, media=media)
    store.secret = b"fixture-secret"
    views = Views(store)
    dev = "dev_k3j9q2m8x4c7v1bz"
    store.devices[dev] = {"id": dev, "name": "Braxton's iPhone", "platform": "ios", "created": FIXED_NOW - 86400,
                          "last_seen": FIXED_NOW - 30, "apns_token": "8f2d" * 16, "environment": "sandbox",
                          "prefs": {"new_catch": True, "rare": False, "discovered": True, "service_alerts": True},
                          "revoked": False}
    h = Handler.__new__(Handler)
    h.store, h.views = store, views

    def route(method, path, q=None):
        return Handler.route(h, method, path, q or {}, dev)[1]

    out = {}
    out["status.json"] = route("GET", "/api/status")
    pg = route("GET", "/api/sightings", {"limit": ["3"]})
    out["sightings_page.json"] = pg
    rows = sorted(store.sightings.values(), key=lambda s: s["started"], reverse=True)
    cloud = next(s for s in rows if s["source"] == "cloud" and s["siglip_label"] != s["label"])
    out["sighting_cloud.json"] = views.sighting(cloud, dev)
    corrected = next(s for s in rows if s["id"] in store.corrections)
    out["sighting_corrected.json"] = views.sighting(corrected, dev)
    out["collection.json"] = route("GET", "/api/collection")
    allh = route("GET", "/api/highlights", {"limit": ["200"]})["items"]
    selectors = [   # one of each shape the app must render
        lambda x: x["id"].startswith("sighting:") and x["types"] == ["new_catch", "rare"],
        lambda x: x["id"].startswith("sighting:") and x["types"] == ["rare"],
        lambda x: x["id"].startswith("sighting:") and "starred" in x["types"],
        lambda x: x["id"].startswith("clip:"),
        lambda x: x["id"].startswith("window:") and "busiest" in x["types"],
        lambda x: x["id"].startswith("window:") and x["clip"] is None and "starred" in x["types"],
        lambda x: x["id"].startswith("daily:"),
    ]
    pick = []
    for sel in selectors:
        hit = next((x for x in allh if sel(x) and x not in pick), None)
        if hit:
            pick.append(hit)
    pick.sort(key=lambda x: (x["at"], x["id"]), reverse=True)
    out["highlights_page.json"] = {"items": pick, "next_cursor": encode_cursor([FIXED_NOW - 86400 * 5, "x"]),
                                   "total": len(allh)}
    clips = route("GET", "/api/clips")
    clips["items"] = clips["items"][:4]
    out["clips.json"] = clips
    out["daily_page.json"] = route("GET", "/api/daily", {"limit": ["3"]})
    out["archive.json"] = route("GET", "/api/archive")
    today_s = next(s for s in rows if clock.day(s["started"]) == clock.day(FIXED_NOW) and
                   any(c["start"] <= s["started"] < c["end"] for c in store.clips()))
    out["seek_clip.json"] = views.seek((today_s["started"] + today_s["ended"]) / 2, dev)
    old_s = next(s for s in rows if clock.day(s["started"]) == clock.day(FIXED_NOW) - timedelta(days=3))
    out["seek_daily.json"] = views.seek((old_s["started"] + old_s["ended"]) / 2, dev)
    out["seek_none.json"] = views.seek(clock.at(clock.day(FIXED_NOW), 2.5), dev)
    out["device.json"] = route("GET", "/api/devices/me")
    out["pair_result.json"] = {"token": "EXAMPLE_pairing_token_not_a_real_secret_000", "device": out["device.json"],
                               "server_name": "CAMPI-PC", "api_version": API_VERSION}
    out["error.json"] = {"error": {"code": "not_found", "message": "no sighting 00000000-0000-4000-8000-000000000000"}}
    out["push_new_catch.json"] = push_payload(store, "new_catch")
    out["push_service.json"] = push_payload(store, "service")

    store_off = Store(clock, seed=7, media=media, sightings_on=False, v1=True)
    store_off.devices[dev] = store.devices[dev]
    out["status_v1_sightings_off.json"] = Views(store_off).status(dev)

    schema = Schema.load()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for old in FIXTURES.glob("*.json"):
        old.unlink()
    bad = []
    for name, obj in out.items():
        (FIXTURES / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        bad += [f"{name}: {e}" for e in schema.validate(obj, schema.fixtures[name])]
    print(f"wrote {len(out)} fixtures to {FIXTURES}")
    if bad:
        print("\n".join(bad))
        sys.exit(1)


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="mock-token", help="dev bearer token accepted without pairing")
    ap.add_argument("--public-url", help="base URL to put in the pairing link (default http://<host>:<port>)")
    ap.add_argument("--tz", help="IANA zone for the mock PC (default: this Mac's)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--pair-code", help="use this pairing code (8 Crockford chars, still single-use); for UI tests")
    ap.add_argument("--sightings-off", action="store_true", help="[sightings] enabled = false (history kept)")
    ap.add_argument("--no-sightings-db", action="store_true", help="no sightings.db at all")
    ap.add_argument("--v1", action="store_true", help="schema v1 / reserved status fields null")
    ap.add_argument("--latency", type=float, default=0.0, help="seconds added to every response")
    ap.add_argument("--live", choices=["ok", "busy", "unreachable", "unavailable"], default="ok",
                    help="live stream state: busy = 503 live_busy, unreachable = 502 pi_unreachable, "
                         "unavailable = status.live.available false and no recent frame")
    ap.add_argument("--rotation", type=int, choices=[0, 90, 180, 270], default=0, help="status.live.rotation")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--write-fixtures", action="store_true")
    ap.add_argument("--print-push", choices=["new_catch", "rare", "discovered", "service"])
    ap.add_argument("--bundle-id", default="com.braxtonmills.campi", help="for --print-push (simctl target)")
    a = ap.parse_args(argv)

    if a.write_fixtures:
        return write_fixtures()
    media = ensure_media()
    clock = Clock(ZoneInfo(a.tz) if a.tz else local_zone())
    store = Store(clock, a.seed, media, sightings_on=not a.sightings_off, has_db=not a.no_sightings_db,
                  v1=a.v1, dev_token=a.token)
    if a.print_push:
        payload = push_payload(store, a.print_push)
        payload["Simulator Target Bundle"] = a.bundle_id
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    store.live_state, store.rotation = a.live, a.rotation
    Handler.store, Handler.views = store, Views(store)
    Handler.latency, Handler.quiet, Handler.bundle_id = a.latency, a.quiet, a.bundle_id
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    base = a.public_url or f"http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}"
    code = store.new_pair_code()
    if a.pair_code:
        store.pair_codes.clear()
        fixed = re.sub(r"[\s-]", "", a.pair_code).upper()
        store.pair_codes[fixed] = clock.now() + 600
        code = f"{fixed[:4]}-{fixed[4:]}"
    print(f"Campi mock PC on {base}  (tz {clock.tz.key}, {len(store.sightings)} sightings, "
          f"{len(store.clips())} clips)", file=sys.stderr)
    print(f"  dev token : {a.token}", file=sys.stderr)
    print(f"  pair code : {code}  (10 min, single use)", file=sys.stderr)
    print(f"  pair link : campi://pair?u={quote(base, safe='')}&c={code.replace('-', '')}", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
