import json
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.keys.queries import list_keys
from app.keys.tables import ApiKey


def key_record(key: ApiKey) -> dict:
    """The JSON the Router reads from `key:<id>` (never contains the plaintext secret)."""
    return {
        "hash": key.secret_hash,
        "allowed_models": list(key.allowed_models or []),
        "allow_all": bool(key.allow_all),
        "expires_at": key.expires_at,
        "name": key.name,
    }


def _is_valid(key: ApiKey, now: float) -> bool:
    if key.revoked_at is not None:
        return False
    return key.expires_at is None or key.expires_at > now


async def publish_key(redis, key: ApiKey, now: float | None = None) -> None:
    now = time.time() if now is None else now
    redis_key = f"key:{key.id}"
    if not _is_valid(key, now):
        await redis.delete(redis_key)
        return
    ttl = None if key.expires_at is None else max(1, int(key.expires_at - now))
    await redis.set(redis_key, json.dumps(key_record(key)), ex=ttl)


async def unpublish_key(redis, key_id: str) -> None:
    await redis.delete(f"key:{key_id}")


async def rebuild_keys(redis, session: AsyncSession, now: float | None = None) -> None:
    """Make Redis match the database: publish valid keys, delete everything else under `key:*`."""
    now = time.time() if now is None else now
    keys = await list_keys(session)
    valid_ids = set()
    for key in keys:
        await publish_key(redis, key, now)
        if _is_valid(key, now):
            valid_ids.add(key.id)
    async for redis_key in redis.scan_iter(match="key:*"):
        if redis_key.removeprefix("key:") not in valid_ids:
            await redis.delete(redis_key)


async def drop_revoked(redis, sessionmaker, key_ids, now: float | None = None) -> None:
    """Deletes from Redis any of `key_ids` that a FRESH read says is revoked or expired.

    Closes a race: a snapshot taken before an admin revoked a key can be written back after the revocation."""
    now = time.time() if now is None else now
    async with sessionmaker() as session:
        for key_id in key_ids:
            key = await session.get(ApiKey, key_id)
            await session.refresh(key) if key is not None else None
            if key is None or not _is_valid(key, now):
                await redis.delete(f"key:{key_id}")
