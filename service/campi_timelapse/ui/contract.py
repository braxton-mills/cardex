"""Conventions of the Campi API contract (campi-ios docs/api-contract.md §2): time format, errors, paging and
strict parameter parsing. Every API module builds its answers with these helpers."""
from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, time as dtime, timedelta

API_VERSION = 1
MAX_LIMIT = 200


class ApiError(Exception):
    """Any non-2xx answer: {"error": {"code", "message"}} (§2.4)."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def not_found(message: str) -> ApiError:
    return ApiError(404, "not_found", message)


def invalid(message: str) -> ApiError:
    return ApiError(400, "invalid_param", message)


# ---------------------------------------------------------------- time (§2.2)

def iso(ts: float | None) -> str | None:
    """Instant as RFC 3339 with milliseconds and the PC's UTC offset at that instant (never Z)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")


def utc_to_ts(s: str | None) -> float | None:
    """sightings.db / ui.db stores ISO 8601 UTC; None stays None."""
    return datetime.fromisoformat(s).timestamp() if s else None


def iso_from_utc(s: str | None) -> str | None:
    return iso(utc_to_ts(s))


def local_day(ts: float) -> date:
    return datetime.fromtimestamp(ts).date()


def midnight(d: date) -> float:
    """Local midnight starting day d (epoch)."""
    return datetime.combine(d, dtime.min).timestamp()


def next_midnight(d: date) -> float:
    return midnight(d + timedelta(days=1))


def parse_instant(s: str) -> float:
    """RFC 3339 with any offset (Z allowed) or Unix seconds; a bare local time is refused."""
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(re.sub(r" (\d{2}:\d{2})$", r"+\1", s.strip()))  # unescaped '+' -> space
    except ValueError:
        raise invalid(f"bad instant {s!r}: use RFC 3339 or Unix seconds") from None
    if dt.tzinfo is None:
        raise invalid(f"instant needs a UTC offset: {s!r}")
    return dt.timestamp()


# ---------------------------------------------------------------- query parameters (strict)

def one(q, name: str) -> str | None:
    vals = q.getlist(name)
    return vals[-1] if vals else None


def int_param(q, name: str, default: int, lo: int, hi: int) -> int:
    v = one(q, name)
    if v is None:
        return default
    try:
        n = int(v)
    except ValueError:
        raise invalid(f"{name} must be an integer") from None
    if not lo <= n <= hi:
        raise invalid(f"{name} must be {lo}-{hi}")
    return n


def bool_param(q, name: str) -> bool:
    v = (one(q, name) or "false").lower()
    if v not in ("true", "false", "1", "0"):
        raise invalid(f"{name} must be true or false")
    return v in ("true", "1")


def date_param(q, name: str) -> date | None:
    v = one(q, name)
    if v is None:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise invalid(f"{name} must be YYYY-MM-DD") from None


def multi(q, name: str, allowed: tuple | None = None) -> list[str]:
    """Repeatable parameter (?class=car&class=truck); a comma list is accepted too."""
    out = [x for v in q.getlist(name) for x in v.split(",") if x]
    if allowed is not None:
        bad = [x for x in out if x not in allowed]
        if bad:
            raise invalid(f"{name} must be one of {', '.join(allowed)} (got {bad[0]!r})")
    return out


def limit_param(q) -> int:
    return int_param(q, "limit", 50, 1, MAX_LIMIT)


# ---------------------------------------------------------------- paging (§2.5)

def encode_cursor(key) -> str:
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).rstrip(b"=").decode()


def decode_cursor(cur: str, n: int | None) -> list:
    """The sort key of the last item of the previous page; anything else is a 400."""
    try:
        key = json.loads(base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4)))
    except Exception:
        raise invalid("bad cursor") from None
    if not isinstance(key, list) or not key or (n is not None and len(key) != n):
        raise invalid("bad cursor")
    return key


def page(items: list, key, q) -> dict:
    """Keyset paging over items already sorted newest first by key(item) (a JSON-able list, compared as a tuple).
    Returns {items, next_cursor, total}; total counts every item matching the filters."""
    limit = limit_param(q)
    cur = one(q, "cursor")
    rest = items
    if cur:
        after = decode_cursor(cur, len(key(items[0])) if items else None)
        rest = [i for i in items if key(i) < after]
    chunk = rest[:limit]
    return {"items": chunk, "next_cursor": encode_cursor(key(chunk[-1])) if len(rest) > limit else None,
            "total": len(items)}


# ---------------------------------------------------------------- bodies

def flag(body: dict, key: str) -> bool:
    v = body.get(key) if isinstance(body, dict) else None
    if not isinstance(v, bool):
        raise invalid(f"body needs {key}: true|false")
    return v
