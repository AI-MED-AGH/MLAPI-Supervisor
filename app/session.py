"""Stateless signed session cookies for the admin UI.

The cookie holds an expiry and a fingerprint of the admin-key hash that logged in, signed with an HMAC.
Removing a key from ADMIN_API_KEYS therefore ends the sessions it created.
"""
import base64
import hashlib
import hmac
import json
import time

COOKIE_NAME = "mlapi_admin_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "mlapi-admin"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def fingerprint(key_hash: str) -> str:
    return hashlib.sha256(b"fp:" + key_hash.encode()).hexdigest()[:16]


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def issue(secret: str, key_hash: str, ttl: float, now: float | None = None) -> str:
    payload = {"exp": int((now if now is not None else time.time()) + ttl), "fp": fingerprint(key_hash)}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body.decode()}.{_sign(secret, body)}"


def verify(secret: str, token: str, valid_hashes: set[str], now: float | None = None) -> bool:
    try:
        encoded, signature = token.rsplit(".", 1)
        body = encoded.encode()
        if not hmac.compare_digest(signature, _sign(secret, body)):
            return False
        payload = json.loads(base64.urlsafe_b64decode(body))
        if payload["exp"] <= (now if now is not None else time.time()):
            return False
        return any(hmac.compare_digest(payload["fp"], fingerprint(h)) for h in valid_hashes)
    except (ValueError, KeyError, TypeError):
        return False


class LoginLimiter:
    """Per-client failed-login throttle (in memory; the Supervisor runs as a single replica)."""

    def __init__(self, max_failures: int = 10, window: float = 300.0):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, list[float]] = {}

    def _recent(self, client: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(client, []) if now - t < self.window]
        if recent:
            self._failures[client] = recent
        else:
            self._failures.pop(client, None)
        return recent

    def blocked(self, client: str, now: float | None = None) -> bool:
        return len(self._recent(client, now if now is not None else time.time())) >= self.max_failures

    def record_failure(self, client: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._recent(client, now)
        self._failures.setdefault(client, []).append(now)

    def reset(self, client: str) -> None:
        self._failures.pop(client, None)
