"""Optional Gemini second opinion for sightings ([sightings.cloud], off by default).

Rows are always written with SigLIP's answer first. Sightings that SigLIP is unsure about (top share below
unsure_below, top-1 minus top-2 below cloud_margin, or the first-ever row with that label) wait in the
cloud_queue table, which survives restarts and outages. One request at a time: the best crop (longest side 384 px)
plus SigLIP's top 3 labels, JSON via structured output, the lowest thinking level the model accepts, 10 s
timeout. Failures retry with backoff (30 s doubling to 1 h). cloud_max_per_day is counted in the DB; over it,
SigLIP's answer stays. The API key is read from api_key_file for each request and never logged.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import cv2

from . import sightings_db as db

log = logging.getLogger("sightings")

PROMPT = """This crop comes from a fixed street camera. Identify the vehicle.
An image model's top guesses (with its confidence shares): {guesses}.
Return the vehicle's make and model (use the guesses' naming style when one fits), an approximate model-year
range like "2019-2023" (empty if you can't tell), its main color, your confidence from 0 to 1, and
in_candidates = true only if your make and model is one of the guesses."""
# Lowest first; the first one the model accepts is remembered for the rest of the run.
THINKING = ({"thinking_level": "MINIMAL"}, {"thinking_level": "LOW"}, {"thinking_budget": 0}, None)
TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


class Permanent(Exception):
    pass


def _schema():
    from pydantic import BaseModel

    class VehicleID(BaseModel):
        make: str
        model: str
        year_range: str
        color: str
        confidence: float
        in_candidates: bool
    return VehicleID


class CloudWorker(threading.Thread):
    def __init__(self, cfg, root: Path, labels, hb_set, poll_s: float = 5.0):
        super().__init__(daemon=True, name="sightings-cloud")
        self.c, self.root, self.labels, self.hb_set, self.poll_s = cfg.sightings.cloud, root, labels, hb_set, poll_s
        self.label_set = {n.lower() for n in labels.names}
        self.stop_evt = threading.Event()
        self.thinking_i = 0
        self.requests = self.updated = self.capped = self.errors = 0
        self.ms = 0.0
        self.schema = _schema()
        self.start()

    # -- helpers
    def _key(self) -> str:
        return Path(self.c.api_key_file).read_text(encoding="utf-8-sig").strip()

    def _clean(self, text: str) -> str:
        """Error text for logs, with the key scrubbed just in case."""
        try:
            key = self._key()
        except OSError:
            key = ""
        text = str(text)
        return (text.replace(key, "***") if key else text)[:300]

    def pending(self) -> int:
        con = db.connect_ro(self.root)
        try:
            return db.cloud_pending(con) if con else 0
        finally:
            if con:
                con.close()

    def stop(self):
        self.stop_evt.set()
        self.join(20)

    # -- main loop
    def run(self):
        con = db.connect(self.root)
        while not self.stop_evt.is_set():
            try:
                self.hb_set(cloud_pending=db.cloud_pending(con), cloud_today=db.cloud_calls_today(con),
                            cloud_cap=self.c.cloud_max_per_day)
                job = db.next_cloud_job(con)
                if job is None:
                    self.stop_evt.wait(self.poll_s)
                    continue
                sid = job["sighting_id"]
                if db.cloud_calls_today(con) >= self.c.cloud_max_per_day:
                    db.finish_cloud_job(con, sid, None, error="cap")  # SigLIP's answer stays
                    self.capped += 1
                    log.info("cloud: daily cap %d reached; keeping SigLIP's answer for %s",
                             self.c.cloud_max_per_day, sid[:8])
                    continue
                try:
                    result = self.ask(job)
                except Permanent as e:
                    self.errors += 1
                    db.finish_cloud_job(con, sid, None, error=self._clean(e))
                    log.error("cloud: %s gave up: %s", sid[:8], self._clean(e))
                    continue
                except Exception as e:  # network, timeout, 429/5xx: retry later (survives restarts)
                    self.errors += 1
                    delay = min(3600, 30 * 2 ** job["attempts"])
                    db.retry_cloud_job(con, sid, self._clean(f"{type(e).__name__}: {e}"), delay)
                    log.warning("cloud: %s failed (%s); retry in %ds", sid[:8], self._clean(type(e).__name__), delay)
                    continue
                self.apply(con, job, result)
            except Exception:
                log.exception("cloud worker error")
                self.stop_evt.wait(10)
        con.close()

    def ask(self, job) -> dict:
        from google import genai
        from google.genai import errors, types
        img = cv2.imread(str(self.root / job["crop_path"]), cv2.IMREAD_COLOR)
        if img is None:
            raise Permanent(f"crop missing: {job['crop_path']}")
        h, w = img.shape[:2]
        s = 384 / max(h, w)
        if s < 1:
            img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
        guesses = [(job["label"], job["confidence"])] + [(r["label"], r["p"]) for r in json.loads(job["runner_ups"])]
        prompt = PROMPT.format(guesses="; ".join(f"{n} ({p:.0%})" for n, p in guesses[:3]))
        try:
            key = self._key()
        except OSError as e:
            raise Permanent(f"api_key_file unreadable: {type(e).__name__}") from None
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=10_000))
        while True:
            think = THINKING[self.thinking_i]
            config = types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=self.schema, temperature=0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=types.ThinkingConfig(**think) if think else None)
            t0 = time.perf_counter()
            try:
                resp = client.models.generate_content(
                    model=self.c.model, config=config,
                    contents=[types.Part.from_bytes(data=jpg, mime_type="image/jpeg"), prompt])
            except errors.APIError as e:
                if e.code == 400 and "think" in str(e).lower() and self.thinking_i < len(THINKING) - 1:
                    self.thinking_i += 1  # this model doesn't take that thinking setting; try the next lowest
                    continue
                if e.code in TRANSIENT_CODES:
                    raise
                raise Permanent(f"HTTP {e.code} {e.status}") from None
            finally:
                self.ms += (time.perf_counter() - t0) * 1000
            self.requests += 1
            parsed = resp.parsed
            if parsed is None:
                raise Permanent("no structured answer")
            return parsed.model_dump()

    def apply(self, con, job, r: dict):
        make, model = (r.get("make") or "").strip(), (r.get("model") or "").strip()
        if not make or not model:
            db.finish_cloud_job(con, job["sighting_id"], r, error="empty answer")
            return
        label = model if model.lower().startswith(make.lower()) else f"{make} {model}"
        update = {"label": label, "make": make, "model": model, "year_range": r.get("year_range") or None,
                  "color": r.get("color") or None, "confidence": round(float(r.get("confidence") or 0), 4),
                  "source": "cloud"}
        db.finish_cloud_job(con, job["sighting_id"], r, update=update)
        self.updated += 1
        if label.lower() not in self.label_set:
            db.record_discovered(con, label, make, model, job["started_at"])
        log.info("cloud: %s %s -> %s %s, %s (%.0f%%)%s", job["sighting_id"][:8], job["label"], label,
                 r.get("year_range") or "", r.get("color") or "", 100 * update["confidence"],
                 "" if label.lower() in self.label_set else "  [new label]")
