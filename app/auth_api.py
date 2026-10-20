from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app import session as sessions
from app.auth import match_admin_key, require_admin

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    key: str = Field(min_length=1, max_length=500)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _secure(request: Request) -> bool:
    configured = request.app.state.settings.session_cookie_secure
    if configured is not None:
        return configured
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: LoginBody, request: Request) -> Response:
    state = request.app.state
    client = _client(request)
    if state.login_limiter.blocked(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts; try again later", headers={"Retry-After": "300"})
    matched = match_admin_key(body.key, state.settings.admin_hashes)
    if matched is None:
        state.login_limiter.record_failure(client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin key")
    state.login_limiter.reset(client)
    ttl = state.settings.session_ttl
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        sessions.COOKIE_NAME,
        sessions.issue(state.session_secret, matched, ttl),
        max_age=int(ttl),
        httponly=True,
        samesite="strict",
        secure=_secure(request),
        path="/",
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    # logout needs no authentication, but it is still a mutation: require the CSRF header so other sites cannot force it
    if request.headers.get(sessions.CSRF_HEADER) != sessions.CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(sessions.COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=_secure(request))
    return response


@router.get("/me", dependencies=[Depends(require_admin)])
async def me() -> dict:
    return {"authenticated": True}
