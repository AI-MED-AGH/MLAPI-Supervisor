import json

from app.keys.queries import create_key, revoke_key
from app.redis_sync.keys import key_record, publish_key, rebuild_keys, unpublish_key


async def make(session, name="k", **kw):
    kw.setdefault("allowed_models", ["m1"])
    kw.setdefault("allow_all", False)
    kw.setdefault("expires_at", None)
    key, raw = await create_key(session, name=name, **kw)
    await session.commit()
    return key, raw


async def test_record_matches_router_contract(session):
    key, _ = await make(session, allowed_models=["ecg-*"], expires_at=2_000_000_000)
    assert key_record(key) == {
        "hash": key.secret_hash,
        "allowed_models": ["ecg-*"],
        "allow_all": False,
        "expires_at": 2_000_000_000,
        "name": "k",
    }


async def test_publish_writes_json_without_plaintext(session, redis):
    key, raw = await make(session)
    await publish_key(redis, key, now=1000)
    stored = await redis.get(f"key:{key.id}")
    assert json.loads(stored)["hash"] == key.secret_hash
    assert raw not in stored


async def test_publish_sets_ttl_from_expiry(session, redis):
    key, _ = await make(session, expires_at=1100)
    await publish_key(redis, key, now=1000)
    assert 0 < await redis.ttl(f"key:{key.id}") <= 100


async def test_expired_or_revoked_key_is_not_published(session, redis):
    expired, _ = await make(session, name="e", expires_at=500)
    revoked, _ = await make(session, name="r")
    await revoke_key(session, revoked, now=1)
    await session.commit()
    await publish_key(redis, expired, now=1000)
    await publish_key(redis, revoked, now=1000)
    assert await redis.exists(f"key:{expired.id}") == 0
    assert await redis.exists(f"key:{revoked.id}") == 0


async def test_publish_removes_previous_value_when_revoked(session, redis):
    key, _ = await make(session)
    await publish_key(redis, key, now=1000)
    await revoke_key(session, key, now=1)
    await publish_key(redis, key, now=1000)
    assert await redis.exists(f"key:{key.id}") == 0


async def test_unpublish(session, redis):
    key, _ = await make(session)
    await publish_key(redis, key, now=1000)
    await unpublish_key(redis, key.id)
    assert await redis.exists(f"key:{key.id}") == 0


async def test_rebuild_restores_valid_keys_and_drops_stale(session, redis):
    good, _ = await make(session, name="good")
    gone, _ = await make(session, name="gone")
    await revoke_key(session, gone, now=1)
    await session.commit()
    await redis.set("key:orphanORPHAN", "{}")      # stale entry no key in the DB owns
    await redis.set("route:m1", "keep-me")          # unrelated keys untouched
    await rebuild_keys(redis, session, now=1000)
    assert await redis.exists(f"key:{good.id}") == 1
    assert await redis.exists(f"key:{gone.id}") == 0
    assert await redis.exists("key:orphanORPHAN") == 0
    assert await redis.get("route:m1") == "keep-me"
