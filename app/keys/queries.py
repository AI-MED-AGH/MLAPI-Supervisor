import time
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keys.domain import generate_key, hash_secret
from app.keys.tables import ApiKey


async def create_key(
    session: AsyncSession,
    *,
    name: str,
    allowed_models: list[str],
    allow_all: bool,
    expires_at: int | None,
) -> tuple[ApiKey, str]:
    key_id, secret, raw = generate_key()
    key = ApiKey(
        id=key_id,
        name=name,
        secret_hash=hash_secret(secret),
        allowed_models=list(allowed_models),
        allow_all=allow_all,
        expires_at=expires_at,
        created_at=int(time.time()),
    )
    session.add(key)
    await session.flush()
    return key, raw


async def list_keys(session: AsyncSession) -> Sequence[ApiKey]:
    return (await session.scalars(select(ApiKey).order_by(ApiKey.created_at, ApiKey.id))).all()


async def get_key(session: AsyncSession, key_id: str) -> ApiKey | None:
    return await session.get(ApiKey, key_id)


async def update_key(session: AsyncSession, key: ApiKey, **fields) -> ApiKey:
    for name, value in fields.items():
        setattr(key, name, value)
    await session.flush()
    return key


async def revoke_key(session: AsyncSession, key: ApiKey, now: int | None = None) -> ApiKey:
    key.revoked_at = int(time.time()) if now is None else now
    await session.flush()
    return key
