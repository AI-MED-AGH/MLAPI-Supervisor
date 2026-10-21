import hashlib
import hmac
import logging

from fastapi import HTTPException, Request, status

from app import session as sessions


audit = logging.getLogger("app.audit")


def hash_admin_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def match_admin_key(raw: str, hashes: set[str]) -> str | None:
    """Returns the stored hash that `raw` matches, or None."""
    candidate = hash_admin_key(raw).encode()
    matched = None
    for stored in hashes:
        # compare against every configured hash so timing does not reveal which one matched
        if hmac.compare_digest(candidate, stored.encode()):
            matched = stored
    return matched if raw else None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing admin key")


def _denied(request: Request) -> HTTPException:
    """A credential was offered and was wrong. Logged without the credential."""
    client = request.client.host if request.client else "unknown"
    audit.warning("denied %s %s from %s", request.method, request.url.path, client)
    return _unauthorized()


def _admitted(request: Request, settings, matched_hash: str) -> None:
    name = settings.admin_names.get(matched_hash, "admin")
    request.state.admin = name  # who approvals and the audit log credit
    if request.method not in sessions.SAFE_METHODS:
        audit.info("admin=%s %s %s", name, request.method, request.url.path)


async def require_admin(request: Request) -> None:
    settings = request.app.state.settings
    raw = request.headers.get("X-Admin-Key")
    if raw is not None:
        matched = match_admin_key(raw, settings.admin_hashes)
        if matched is None:
            raise _denied(request)
        _admitted(request, settings, matched)
        return

    secret = getattr(request.app.state, "session_secret", None)
    token = request.cookies.get(sessions.COOKIE_NAME)
    if secret and token:
        matched = sessions.matching_hash(secret, token, settings.admin_hashes)
        if matched is not None:
            # a cookie is sent by the browser on its own, so mutations must prove they come from our UI
            # (a custom header cannot be added cross-site without a CORS preflight, which we never allow)
            if request.method not in sessions.SAFE_METHODS and request.headers.get(sessions.CSRF_HEADER) != sessions.CSRF_VALUE:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing CSRF header")
            _admitted(request, settings, matched)
            return
    raise _unauthorized()
