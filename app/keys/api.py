import logging
import re
import time

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.deps import get_redis, get_settings_dep
from app.limits import KEY_LIMIT_FIELDS, validate_limits
from app.keys import queries
from app.keys.tables import ApiKey
from app.redis_sync.keys import publish_key, unpublish_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/keys", tags=["keys"], dependencies=[Depends(require_admin)])

_PATTERN_RE = re.compile(r"[a-z0-9][-a-z0-9]{0,38}\*?")


def _check_patterns(models: list[str]) -> list[str]:
    for pattern in models:
        if not _PATTERN_RE.fullmatch(pattern):
            raise ValueError(f"invalid model pattern: {pattern!r}")
    return models


def _check_limits(value):
    return validate_limits(value, KEY_LIMIT_FIELDS)


def _check_future(value: int | None) -> int | None:
    if value is not None and value <= time.time():
        raise ValueError("expires_at must be in the future")
    return value


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    allowed_models: list[str] = Field(default_factory=list)
    allow_all: bool = False
    expires_at: int | None = None
    limits: Any = Field(default_factory=dict)

    _patterns = field_validator("allowed_models")(_check_patterns)
    _future = field_validator("expires_at")(_check_future)
    _limits = field_validator("limits", mode="before")(_check_limits)

    @model_validator(mode="after")
    def _consistent(self):
        if self.allow_all and self.allowed_models:
            raise ValueError("allow_all and allowed_models are mutually exclusive")
        if not self.allow_all and not self.allowed_models:
            raise ValueError("a key must allow at least one model or set allow_all")
        return self


class KeyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    allowed_models: list[str] | None = None
    allow_all: bool | None = None
    expires_at: int | None = None
    limits: Any = None

    _limits = field_validator("limits", mode="before")(_check_limits)

    @field_validator("allowed_models")
    @classmethod
    def _patterns(cls, v):
        return _check_patterns(v) if v is not None else v

    _future = field_validator("expires_at")(_check_future)


class KeyOut(BaseModel):
    id: str
    name: str
    allowed_models: list[str]
    allow_all: bool
    expires_at: int | None
    limits: dict[str, int]
    revoked_at: int | None
    created_at: int

    @classmethod
    def of(cls, key: ApiKey) -> "KeyOut":
        return cls(
            id=key.id,
            name=key.name,
            allowed_models=list(key.allowed_models or []),
            allow_all=key.allow_all,
            expires_at=key.expires_at,
            limits=dict(key.limits or {}),
            revoked_at=key.revoked_at,
            created_at=key.created_at,
        )


class KeyCreated(KeyOut):
    key: str


def _check_lifetime(settings, expires_at: int | None) -> None:
    """With a maximum lifetime configured, a key must expire, and not too far ahead."""
    longest = settings.key_max_lifetime_seconds
    if longest <= 0:
        return
    if expires_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"keys must expire within {longest} seconds")
    if expires_at > time.time() + longest:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"expires_at must be within {longest} seconds from now")


async def _redis_or_503(session: AsyncSession, call):
    try:
        await call
    except RedisError:
        await session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Redis unavailable")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=KeyCreated)
async def create_key(
    body: KeyCreate,
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis),
    settings=Depends(get_settings_dep),
):
    _check_lifetime(settings, body.expires_at)
    key, raw = await queries.create_key(
        session,
        name=body.name,
        allowed_models=body.allowed_models,
        allow_all=body.allow_all,
        expires_at=body.expires_at,
        limits=body.limits,
    )
    await _redis_or_503(session, publish_key(redis, key))
    await session.commit()
    return KeyCreated(**KeyOut.of(key).model_dump(), key=raw)


@router.get("", response_model=list[KeyOut])
async def list_keys(session: AsyncSession = Depends(get_session)):
    return [KeyOut.of(k) for k in await queries.list_keys(session)]


@router.patch("/{key_id}", response_model=KeyOut)
async def patch_key(
    key_id: str,
    body: KeyPatch,
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis),
    settings=Depends(get_settings_dep),
):
    key = await queries.get_key(session, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    if key.revoked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Key is revoked")
    if "expires_at" in body.model_fields_set:
        _check_lifetime(settings, body.expires_at)
    changes = {f: getattr(body, f) for f in body.model_fields_set}
    await queries.update_key(session, key, **changes)
    if not key.allow_all and not key.allowed_models:
        await session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "a key must allow at least one model or set allow_all")
    await _redis_or_503(session, publish_key(redis, key))
    await session.commit()
    return KeyOut.of(key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: str, session: AsyncSession = Depends(get_session), redis=Depends(get_redis)
):
    key = await queries.get_key(session, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    if key.revoked_at is None:
        await queries.revoke_key(session, key)
    await _redis_or_503(session, unpublish_key(redis, key.id))  # first: a Redis outage must fail the request, not the key
    await session.commit()
    try:  # again after the commit: a reconciler pass may have re-added a stale copy in between
        await unpublish_key(redis, key.id)
    except RedisError:
        logger.warning("Second Redis delete of a revoked key failed; the reconciler will remove it")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


USAGE_DAYS_MAX = 35  # the Router keeps its counters this long


@router.get("/{key_id}/usage")
async def key_usage(
    key_id: str,
    days: int = Query(7, ge=1, le=USAGE_DAYS_MAX),
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis),
):
    """What a key has been used for, from the counters the Router keeps: requests, model errors (5xx) and refusals
    (429) per model and UTC day. `-` is requests that ended before a model was identified."""
    if await queries.get_key(session, key_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    now = time.time()
    names = [time.strftime("%Y%m%d", time.gmtime(now - n * 86400)) for n in range(days)]
    try:
        rows = [await redis.hgetall(f"usage:{name}:{key_id}") for name in names]
    except RedisError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Redis unavailable")
    report, last_used = [], None
    for name, row in zip(names, rows):
        if not row:
            continue
        models: dict[str, dict[str, int]] = {}
        for field, value in row.items():
            if field == "last_used":
                last_used = max(last_used or 0, int(value))
                continue
            model, _, counter = field.rpartition(":")
            if counter in ("requests", "errors", "limited"):
                models.setdefault(model, {"requests": 0, "errors": 0, "limited": 0})[counter] = int(value)
        report.append({"day": name, "models": models})
    return {"key": key_id, "last_used": last_used, "days": report}
