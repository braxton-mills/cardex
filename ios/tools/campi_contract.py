"""Shape validation for the Campi API contract (contract/schema.json). Stdlib only.

    from campi_contract import Schema
    errors = Schema.load().validate(obj, "Sighting")   # [] when valid
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "contract" / "schema.json"
FIXTURES = ROOT / "contract" / "fixtures"

DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Schema:
    def __init__(self, raw: dict):
        self.types: dict = raw["types"]
        self.endpoints: dict = raw["endpoints"]
        self.fixtures: dict = raw.get("fixtures", {})

    @classmethod
    def load(cls, path: Path = SCHEMA_PATH) -> "Schema":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def validate(self, value, typ, path: str = "$") -> list[str]:
        errors: list[str] = []
        self._check(value, typ, path, errors)
        return errors

    # -- internals
    def _check(self, v, t, path: str, errors: list[str]) -> None:
        if isinstance(t, dict):
            self._object(v, t, path, errors)
            return
        nullable = t.endswith("?")
        if nullable:
            t = t[:-1]
        if v is None:
            if not nullable:
                errors.append(f"{path}: null but not nullable ({t})")
            return
        if t.startswith("[") and t.endswith("]"):
            if not isinstance(v, list):
                errors.append(f"{path}: expected array, got {type(v).__name__}")
                return
            for i, item in enumerate(v):
                self._check(item, t[1:-1], f"{path}[{i}]", errors)
            return
        if t.startswith("enum:"):
            allowed = t[5:].split("|")
            if not isinstance(v, str) or v not in allowed:
                errors.append(f"{path}: {v!r} not in {allowed}")
            return
        if t in self.types:
            self._object(v, self.types[t], path, errors)
            return
        check = PRIMITIVES.get(t)
        if check is None:
            errors.append(f"{path}: schema bug, unknown type {t!r}")
            return
        problem = check(v)
        if problem:
            errors.append(f"{path}: {problem}")

    def _object(self, v, spec: dict, path: str, errors: list[str]) -> None:
        if not isinstance(v, dict):
            errors.append(f"{path}: expected object, got {type(v).__name__}")
            return
        for key, sub in spec.items():
            if key not in v:
                errors.append(f"{path}.{key}: missing")
                continue
            self._check(v[key], sub, f"{path}.{key}", errors)


def _string(v):
    return None if isinstance(v, str) else f"expected string, got {v!r}"


def _int(v):
    return None if isinstance(v, int) and not isinstance(v, bool) else f"expected int, got {v!r}"


def _number(v):
    return None if isinstance(v, (int, float)) and not isinstance(v, bool) else f"expected number, got {v!r}"


def _bool(v):
    return None if isinstance(v, bool) else f"expected bool, got {v!r}"


def _datetime(v):
    if not isinstance(v, str) or not DATETIME_RE.match(v):
        return f"expected RFC 3339 with milliseconds and a numeric offset (no Z), got {v!r}"
    return None


def _date(v):
    return None if isinstance(v, str) and DATE_RE.match(v) else f"expected YYYY-MM-DD, got {v!r}"


def _url(v):
    if not isinstance(v, str) or not v.startswith("/") or v.startswith("//"):
        return f"expected a path-absolute URL, got {v!r}"
    return None


def _signed_url(v):
    problem = _url(v)
    if problem:
        return problem
    q = parse_qs(urlsplit(v).query)
    missing = [k for k in ("d", "exp", "sig") if k not in q]
    if missing:
        return f"signed URL lacks {missing}: {v!r}"
    if not q["exp"][0].isdigit():
        return f"exp must be Unix seconds: {v!r}"
    return None


PRIMITIVES = {"string": _string, "int": _int, "number": _number, "bool": _bool, "datetime": _datetime,
              "date": _date, "url": _url, "signed_url": _signed_url}


def check_fixtures(schema: Schema | None = None) -> list[str]:
    """Validate every fixture named in schema.json; also flag fixture files the schema doesn't list."""
    schema = schema or Schema.load()
    errors = []
    for name, typ in schema.fixtures.items():
        p = FIXTURES / name
        if not p.exists():
            errors.append(f"{name}: missing fixture")
            continue
        errors += [f"{name}: {e}" for e in schema.validate(json.loads(p.read_text(encoding="utf-8")), typ)]
    for p in sorted(FIXTURES.glob("*.json")):
        if p.name not in schema.fixtures:
            errors.append(f"{p.name}: not listed in schema.json fixtures")
    return errors


if __name__ == "__main__":
    import sys
    errs = check_fixtures()
    print("\n".join(errs) or f"all {len(Schema.load().fixtures)} fixtures valid")
    sys.exit(1 if errs else 0)
