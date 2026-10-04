"""Authentication (contract §3): per-device bearer tokens, one-time pairing codes and signed media URLs.

- A token is 32 random bytes (base64url, 43 chars); ui.db stores only its SHA-256. Every lookup reads ui.db, so a
  device revoked by `campi devices revoke` (another process) fails on its very next request.
- Signed URLs: sig = base64url(HMAC-SHA256(server_secret, "<d>|<path>|<exp>")) over the path exactly as sent
  (percent-encoded, from the ASGI raw_path), so documented parameters (max_fps, w) can be appended freely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from urllib.parse import quote

from .contract import ApiError

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
DEVICE_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
PAIR_TTL_S = 600
PLATFORMS = ("ios", "desktop", "other")


def new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_device_id() -> str:
    return "dev_" + "".join(secrets.choice(DEVICE_ALPHABET) for _ in range(16))


def new_pair_code() -> str:
    """8 Crockford base32 chars, stored without the dash."""
    return "".join(secrets.choice(CROCKFORD) for _ in range(8))


def show_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize_code(text: str) -> str:
    """Case-insensitive, ignoring '-' and spaces; Crockford's look-alikes (O, I, L) read as 0, 1, 1."""
    c = re.sub(r"[\s-]", "", str(text)).upper()
    return c.translate(str.maketrans("OIL", "011"))


def create_device(store, name: str, platform: str) -> tuple[str, dict]:
    token = new_token()
    dev = store.add_device(new_device_id(), name, platform, token_hash(token))
    return token, dev


class RateLimit:
    """At most n events per window_s across all callers."""

    def __init__(self, n: int, window_s: float):
        self.n, self.window_s = n, window_s
        self.times: deque[float] = deque()
        self.lock = threading.Lock()

    def hit(self) -> bool:
        now = time.monotonic()
        with self.lock:
            while self.times and self.times[0] <= now - self.window_s:
                self.times.popleft()
            if len(self.times) >= self.n:
                return False
            self.times.append(now)
            return True


class Auth:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.secret = store.server_secret()
        self.ttl_s = int(float(cfg.api.media_url_ttl_h) * 3600)
        self.pair_limit = RateLimit(10, 600)

    # ------------------------------------------------------------ signing
    def _mac(self, device_id: str, path: str, exp: int) -> str:
        mac = hmac.new(self.secret, f"{device_id}|{path}|{exp}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()

    def sign(self, path: str, device_id: str) -> str:
        """path is percent-encoded already (as the client will send it)."""
        exp = int(time.time() + self.ttl_s)
        return f"{path}?d={device_id}&exp={exp}&sig={self._mac(device_id, path, exp)}"

    def verify(self, raw_path: str, q) -> dict:
        d, exp, sig = q.get("d"), q.get("exp"), q.get("sig")
        if not (d and exp and sig):
            raise ApiError(401, "unauthorized", "missing bearer token or signature")
        try:
            exp_i = int(exp)
        except ValueError:
            raise ApiError(401, "unauthorized", "bad signature") from None
        dev = self.store.device(d)
        if not dev or dev["revoked_at"] or not hmac.compare_digest(self._mac(d, raw_path, exp_i), sig):
            raise ApiError(401, "unauthorized", "bad signature")
        if exp_i < time.time():
            raise ApiError(403, "expired", "signed URL expired")
        return dev

    # ------------------------------------------------------------ bearer
    def bearer(self, request) -> dict | None:
        """The calling device, None without an Authorization header; 401 for an unknown or revoked token."""
        h = request.headers.get("authorization", "")
        if not h:
            return None
        scheme, _, token = h.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise ApiError(401, "unauthorized", "Authorization must be 'Bearer <token>'")
        dev = self.store.device_by_token(token_hash(token.strip()))
        if not dev:
            raise ApiError(401, "unauthorized", "unknown or revoked token")
        self.store.touch(dev["id"])
        return dev

    def require(self, request) -> dict:
        dev = self.bearer(request)
        if dev is None:
            raise ApiError(401, "unauthorized", "missing bearer token")
        request.state.device = dev
        return dev

    def media(self, request) -> dict:
        """Bearer token or a valid signature for the raw path (§3.3)."""
        dev = self.bearer(request)
        if dev is None:
            raw = request.scope.get("raw_path") or request.url.path.encode()
            dev = self.verify(raw.decode("latin-1"), request.query_params)
        request.state.device = dev
        return dev

    # ------------------------------------------------------------ pairing
    def pair(self, body: dict) -> tuple[str, dict]:
        if not self.pair_limit.hit():
            raise ApiError(429, "rate_limited", "too many pairing attempts; wait a few minutes")
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_param", "body must be an object")
        code, name, platform = body.get("code"), body.get("device_name"), body.get("platform", "other")
        if not isinstance(code, str) or not isinstance(name, str) or not name.strip() or len(name) > 100 \
                or platform not in PLATFORMS:
            raise ApiError(400, "invalid_param", "need code, device_name and platform (ios | desktop | other)")
        if not self.store.use_pair_code(normalize_code(code)):
            raise ApiError(401, "invalid_code", "pairing code is wrong, used or expired")
        return create_device(self.store, name.strip(), platform)


def media_path(root: str, rel: str) -> str:
    """Percent-encoded /media path (rel is posix, unencoded): the form that gets signed and requested."""
    return f"/media/{root}/{quote(rel)}"
