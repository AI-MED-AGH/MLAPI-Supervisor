import hashlib
import hmac

from fastapi import HTTPException, Request, status


def hash_admin_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def require_admin(request: Request) -> None:
    raw = request.headers.get("X-Admin-Key", "")
    candidate = hash_admin_key(raw).encode()
    ok = False
    for stored in request.app.state.settings.admin_hashes:
        # compare against every configured hash so timing does not reveal which one matched
        ok |= hmac.compare_digest(candidate, stored.encode())
    if not raw or not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin key",
        )
