import json
import time

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.conftest import ADMIN_HEADERS

FUTURE = int(time.time()) + 3600


async def create(api, **body):
    body.setdefault("name", "ci")
    body.setdefault("allowed_models", ["m1"])
    return await api.post("/v1/keys", headers=ADMIN_HEADERS, json=body)


@pytest.mark.parametrize(
    "method,path",
    [("get", "/v1/keys"), ("post", "/v1/keys"), ("patch", "/v1/keys/abc"), ("delete", "/v1/keys/abc")],
)
async def test_every_key_endpoint_requires_admin(api, method, path):
    r = await getattr(api, method)(path)
    assert r.status_code == 401


async def test_create_returns_plaintext_once_and_publishes(api, redis):
    r = await create(api, allowed_models=["ecg-*", "xray"], expires_at=FUTURE)
    assert r.status_code == 201
    body = r.json()
    assert body["key"].startswith("mlapi_") and body["id"] in body["key"]
    stored = json.loads(await redis.get(f"key:{body['id']}"))
    assert stored["allowed_models"] == ["ecg-*", "xray"]
    assert body["key"] not in json.dumps(stored)
    assert "hash" not in body and "secret_hash" not in body


async def test_list_and_get_never_expose_secrets(api):
    created = (await create(api)).json()
    listing = await api.get("/v1/keys", headers=ADMIN_HEADERS)
    assert listing.status_code == 200
    assert created["key"] not in listing.text
    assert "hash" not in listing.text
    assert [k["id"] for k in listing.json()] == [created["id"]]
    assert "key" not in listing.json()[0]


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "allowed_models": []},                              # grants nothing
        {"name": "x", "allowed_models": ["*"]},                           # bare wildcard
        {"name": "x", "allowed_models": ["UPPER"]},
        {"name": "x", "allowed_models": ["a b"]},
        {"name": "x", "allowed_models": ["../etc"]},
        {"name": "", "allowed_models": ["m1"]},
        {"name": "x", "allowed_models": ["m1"], "expires_at": 1},         # in the past
        {"name": "x", "allowed_models": ["m1"], "allow_all": True},       # ambiguous
        {"allowed_models": ["m1"]},                                        # name missing
    ],
)
async def test_create_validation(api, body):
    r = await api.post("/v1/keys", headers=ADMIN_HEADERS, json=body)
    assert r.status_code == 422


async def test_allow_all_key(api, redis):
    r = await create(api, allowed_models=[], allow_all=True)
    assert r.status_code == 201
    assert json.loads(await redis.get(f"key:{r.json()['id']}"))["allow_all"] is True


async def test_patch_updates_db_and_redis(api, redis):
    key_id = (await create(api)).json()["id"]
    r = await api.patch(
        f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"allowed_models": ["m2"], "expires_at": FUTURE}
    )
    assert r.status_code == 200
    assert r.json()["allowed_models"] == ["m2"]
    assert json.loads(await redis.get(f"key:{key_id}"))["allowed_models"] == ["m2"]


async def test_patch_can_clear_expiry(api, redis):
    key_id = (await create(api, expires_at=FUTURE)).json()["id"]
    r = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"expires_at": None})
    assert r.json()["expires_at"] is None
    assert json.loads(await redis.get(f"key:{key_id}"))["expires_at"] is None


async def test_patch_unknown_key_is_404(api):
    assert (await api.patch("/v1/keys/nope", headers=ADMIN_HEADERS, json={"name": "z"})).status_code == 404


async def test_revoke_removes_from_redis_immediately(api, redis):
    key_id = (await create(api)).json()["id"]
    assert await redis.exists(f"key:{key_id}") == 1
    r = await api.delete(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS)
    assert r.status_code == 204
    assert await redis.exists(f"key:{key_id}") == 0
    listed = (await api.get("/v1/keys", headers=ADMIN_HEADERS)).json()
    assert listed[0]["revoked_at"] is not None


async def test_revoke_is_idempotent_and_404_for_unknown(api):
    key_id = (await create(api)).json()["id"]
    assert (await api.delete(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS)).status_code == 204
    assert (await api.delete(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS)).status_code == 204
    assert (await api.delete("/v1/keys/unknownunkn", headers=ADMIN_HEADERS)).status_code == 404


async def test_patch_revoked_key_is_409(api):
    key_id = (await create(api)).json()["id"]
    await api.delete(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS)
    r = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"name": "x"})
    assert r.status_code == 409


async def test_redis_failure_on_create_is_503_and_nothing_persisted(api, redis, monkeypatch):
    async def boom(*a, **k):
        raise RedisConnectionError("down")

    monkeypatch.setattr(redis, "set", boom)
    r = await create(api)
    assert r.status_code == 503
    monkeypatch.undo()
    assert (await api.get("/v1/keys", headers=ADMIN_HEADERS)).json() == []


async def test_redis_failure_on_revoke_keeps_key_active(api, redis, monkeypatch):
    key_id = (await create(api)).json()["id"]

    async def boom(*a, **k):
        raise RedisConnectionError("down")

    monkeypatch.setattr(redis, "delete", boom)
    r = await api.delete(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS)
    assert r.status_code == 503
    monkeypatch.undo()
    assert (await api.get("/v1/keys", headers=ADMIN_HEADERS)).json()[0]["revoked_at"] is None
