"""Claude looks at the water tower site for `campi site`.

- A log of each day (`day_log`): what stood out beyond the concrete pours the activity detector already finds (the
  excavator working, a crew on top on a day without a pour, a delivery, a crane), from views spread over the day. The
  intro's replay shows those, next to the hand-kept intro.EXTRAS.
- The chapter captions (`captions`), one call for the whole video like the Gemini version it replaces (Gemini stays
  the fallback).

The key is `secrets\\anthropic.key` under the data folder (outside the repo, never logged), else ANTHROPIC_API_KEY.
Every answer is cached in state\\site\\, so a re-render only asks about new days or changed chapters.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

import cv2
import numpy as np

from .config import read_json, write_json
from .focus import view

log = logging.getLogger("site")

MODEL = "claude-opus-5-5"
PRICE = (4.0, 20.0)            # $ per million input / output tokens (Claude Opus 5.5), for the log line
IMG = (1024, 768)              # what each view is sent at
DAY_VIEWS = 6                  # spread over a day's daylight, plus its two busiest moments
KINDS = ("dig", "crew", "delivery", "crane", "other")   # dig and crew animate in the intro; the rest are captions
SCHEMA_LOG = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "events": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "caption": {"type": "string"}},
            "required": ["kind", "caption"], "additionalProperties": False}},
    },
    "required": ["summary", "events"], "additionalProperties": False,
}
SCHEMA_CAPS = {
    "type": "object",
    "properties": {"captions": {"type": "array", "items": {
        "type": "object",
        "properties": {"tag": {"type": "string"}, "title": {"type": "string"}, "detail": {"type": "string"}},
        "required": ["tag", "title", "detail"], "additionalProperties": False}}},
    "required": ["captions"], "additionalProperties": False,
}

LOG_PROMPT = """These are {n} views of a construction site on {day:%A %B %d, %Y}, from a fixed camera across the road,
in time order (each is labelled with its local time). It is a 0.5 MG composite elevated water tank: the concrete
cylinder on the left is its pedestal, poured in jump-form lifts with blue climbing forms on top; later a steel bowl is
built at ground level and jacked up. Known facts:
{facts}

Concrete pours are tracked separately ({pours}), so don't report a pour, the boom pump or the mixer trucks that come
with it. Report what else stands out that day, at most two things, only if you can clearly see it in the views:
- dig: an excavator or other earthmoving machine working (digging, moving the spoil pile, grading),
- crew: workers up on the pedestal or the forms on a day with no pour (resetting or raising the forms),
- delivery: a truck delivering materials (steel, form panels, equipment), not a concrete mixer,
- crane: a crane lifting something,
- other: anything else a viewer would notice that changes the site (fencing going up, a new container or trailer).
Routine things (parked pickups, the telehandler moving about, porta-potties) are not events; a quiet day has none.
For each event, caption: what a viewer sees, at most 5 words, in capitals (e.g. EXCAVATOR ON THE SPOIL PILE).
summary: one plain sentence on what the crew did that day, from what is visible (no guessing about what can't be
seen)."""


def api_key(cfg) -> str | None:
    p = cfg.paths.data / "secrets" / "anthropic.key"
    if p.is_file():
        return p.read_text(encoding="utf-8").strip() or None
    return os.environ.get("ANTHROPIC_API_KEY") or None


def available(cfg) -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return api_key(cfg) is not None


def jpeg_block(img: np.ndarray) -> dict:
    data = cv2.imencode(".jpg", cv2.resize(img, IMG, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 88])[1]
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(data.tobytes()).decode("ascii")}}


def ask(cfg, content: list[dict], schema: dict, effort: str = "high", what: str = "") -> dict | None:
    """One structured answer from Claude, or None (no key, an API error or a refusal: the caller falls back)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key(cfg), max_retries=3, timeout=300.0)
    try:
        with client.beta.messages.stream(
                model=MODEL, max_tokens=32000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": content}]) as stream:
            resp = stream.get_final_message()
    except anthropic.AuthenticationError:
        log.warning("claude: the API key was rejected (secrets\\anthropic.key)")
        return None
    except anthropic.APIStatusError as e:
        log.warning("claude %s failed: %s (request %s)", what, e.message, e.request_id)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("claude %s failed: %s", what, e)
        return None
    u = resp.usage
    cost = (u.input_tokens * PRICE[0] + u.output_tokens * PRICE[1]) / 1e6
    log.info("claude %s: %d in / %d out tokens (~$%.3f)", what, u.input_tokens, u.output_tokens, cost)
    if resp.stop_reason == "refusal":
        log.warning("claude %s declined (%s)", what, getattr(resp.stop_details, "category", None))
        return None
    if resp.stop_reason == "max_tokens":
        log.warning("claude %s ran out of tokens", what)
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("claude %s returned no JSON", what)
        return None


# --- the day log ----------------------------------------------------------------------------------------------------

def day_views(cfg, d, box, w: int, h: int) -> list:
    """A day's samples to show: DAY_VIEWS spread over its daylight plus its two busiest, in time order."""
    ss = sorted(d.samples, key=lambda s: s.ts)
    if not ss:
        return []
    picks = {ss[round(i * (len(ss) - 1) / max(1, DAY_VIEWS - 1))].file for i in range(DAY_VIEWS)}
    picks |= {s.file for s in sorted(ss, key=lambda s: s.score, reverse=True)[:2] if s.active}
    return [s for s in ss if s.file in picks]


def day_log(cfg, days: list, box, w: int, h: int, pours: list[float], facts: str, cache: Path) -> dict:
    """{date: {"summary", "events": [{"kind", "caption"}]}} for every day Claude has looked at. A day is asked once
    for the views it had; today again as new frames come in."""
    have = read_json(cache, {}) if cache.is_file() else {}
    if not isinstance(have, dict):
        have = {}
    if available(cfg):
        for d in days:
            picks = day_views(cfg, d, box, w, h)
            key = [s.file for s in picks]
            if not picks or have.get(d.day.isoformat(), {}).get("key") == key:
                continue
            n = sum(datetime.fromtimestamp(p).date() == d.day for p in pours)
            content = []
            for s in picks:
                content.append({"type": "text", "text": f"{datetime.fromtimestamp(s.ts):%H:%M}"})
                content.append(jpeg_block(view(cv2.imread(str(cfg.paths.frames / s.file), cv2.IMREAD_COLOR),
                                               np.array(s.m), box, w, h)))
            content.append({"type": "text", "text": LOG_PROMPT.format(
                n=len(picks), day=d.day, facts=facts,
                pours=f"{n} pour{'s' if n != 1 else ''} seen this day" if n else "none seen this day")})
            got = ask(cfg, content, SCHEMA_LOG, what=f"log {d.day}")
            if got is None:
                continue
            evs = [e for e in got.get("events", []) if e.get("kind") in KINDS and e.get("caption")][:2]
            have[d.day.isoformat()] = {"key": key, "summary": str(got.get("summary", ""))[:300],
                                       "events": [{"kind": e["kind"], "caption": str(e["caption"]).upper()[:40]}
                                                  for e in evs]}
            write_json(cache, have)
    return {date.fromisoformat(k): v for k, v in have.items()}


# --- the captions ----------------------------------------------------------------------------------------------------

def captions(cfg, prompt: str, imgs: list[list[np.ndarray]], n: int) -> list[dict] | None:
    """The whole video's captions in one call: each chapter's views (start, busiest, end), then the prompt."""
    if not available(cfg):
        return None
    content = []
    for i, ims in enumerate(imgs):
        content.append({"type": "text", "text": f"Chapter {i + 1}: its start, its busiest moment, its end"})
        content += [jpeg_block(im) for im in ims]
    content.append({"type": "text", "text": prompt})
    got = ask(cfg, content, SCHEMA_CAPS, what="captions")
    caps = got.get("captions") if got else None
    if not caps or len(caps) != n:
        if caps:
            log.warning("claude returned %d captions for %d chapters", len(caps), n)
        return None
    return caps
