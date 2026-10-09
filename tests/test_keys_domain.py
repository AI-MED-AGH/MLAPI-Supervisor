import hashlib
import re

from app.keys.domain import generate_key, hash_secret, parse_key
from app.keys.queries import create_key, get_key, list_keys, revoke_key, update_key

KEY_RE = re.compile(r"^mlapi_[A-Za-z0-9]{12}_[A-Za-z0-9_-]{43}$")


def test_generate_key_format_and_uniqueness():
    seen = set()
    for _ in range(50):
        key_id, secret, raw = generate_key()
        assert KEY_RE.match(raw)
        assert raw == f"mlapi_{key_id}_{secret}"
        seen.add(raw)
    assert len(seen) == 50


def test_hash_matches_router_algorithm():
    assert hash_secret("s") == hashlib.sha256(b"s").hexdigest()


def test_parse_key_roundtrip_and_rejects_garbage():
    key_id, secret, raw = generate_key()
    assert parse_key(raw) == (key_id, secret)
    assert parse_key("nope") is None
    assert parse_key(raw + "x") is None


async def test_create_and_get(session):
    key, raw = await create_key(session, name="ci", allowed_models=["ecg-*"], allow_all=False, expires_at=None)
    await session.commit()
    assert KEY_RE.match(raw)
    assert key.secret_hash == hash_secret(parse_key(raw)[1])
    assert raw not in repr(key.__dict__.values())
    fetched = await get_key(session, key.id)
    assert fetched.name == "ci" and fetched.allowed_models == ["ecg-*"]


async def test_list_update_revoke(session):
    a, _ = await create_key(session, name="a", allowed_models=["a"], allow_all=False, expires_at=None)
    b, _ = await create_key(session, name="b", allowed_models=[], allow_all=True, expires_at=None)
    await session.commit()
    assert {k.name for k in await list_keys(session)} == {"a", "b"}
    await update_key(session, a, allowed_models=["a", "c"], expires_at=999)
    await session.commit()
    assert (await get_key(session, a.id)).allowed_models == ["a", "c"]
    await revoke_key(session, b, now=123)
    await session.commit()
    assert (await get_key(session, b.id)).revoked_at == 123


async def test_get_unknown_key_is_none(session):
    assert await get_key(session, "doesnotexist") is None
