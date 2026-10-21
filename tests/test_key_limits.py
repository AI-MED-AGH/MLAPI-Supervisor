import json
import time

import pytest

from tests.conftest import ADMIN_HEADERS


async def create(api, **body):
    body.setdefault("name", "ci")
    body.setdefault("allowed_models", ["m1"])
    return await api.post("/v1/keys", headers=ADMIN_HEADERS, json=body)


async def test_a_key_without_limits_has_an_empty_set(api, redis):
    r = await create(api)
    assert r.status_code == 201 and r.json()["limits"] == {}
    assert "limits" not in json.loads(await redis.get(f"key:{r.json()['id']}")) or \
        json.loads(await redis.get(f"key:{r.json()['id']}"))["limits"] == {}


async def test_limits_are_stored_returned_and_published_for_the_router(api, redis):
    limits = {"rpm": 30, "poll_rpm": 0, "jobs_per_day": 200, "max_outstanding_jobs": 10}
    r = await create(api, limits=limits)
    assert r.status_code == 201
    assert r.json()["limits"] == limits
    assert json.loads(await redis.get(f"key:{r.json()['id']}"))["limits"] == limits
    listed = (await api.get("/v1/keys", headers=ADMIN_HEADERS)).json()
    assert listed[0]["limits"] == limits


async def test_patch_changes_limits_and_republishes(api, redis):
    key_id = (await create(api, limits={"rpm": 30})).json()["id"]
    r = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"limits": {"rpm": 5, "poll_rpm": 9}})
    assert r.status_code == 200 and r.json()["limits"] == {"rpm": 5, "poll_rpm": 9}
    assert json.loads(await redis.get(f"key:{key_id}"))["limits"] == {"rpm": 5, "poll_rpm": 9}


async def test_patch_can_clear_limits_back_to_the_defaults(api, redis):
    key_id = (await create(api, limits={"rpm": 30})).json()["id"]
    r = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"limits": {}})
    assert r.json()["limits"] == {}
    assert json.loads(await redis.get(f"key:{key_id}")).get("limits", {}) == {}


async def test_patching_something_else_keeps_the_limits(api):
    key_id = (await create(api, limits={"rpm": 30})).json()["id"]
    r = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"name": "renamed"})
    assert r.json()["limits"] == {"rpm": 30}


@pytest.mark.parametrize(
    "limits",
    [
        {"rpm": -1},
        {"rpm": 1.5},
        {"rpm": "10"},
        {"rpm": True},
        {"rpm": 10_000_001},
        {"requests_per_minute": 10},  # a typo must not silently do nothing
        [],
        "fast",
    ],
)
async def test_invalid_limits_are_rejected(api, limits):
    assert (await create(api, limits=limits)).status_code == 422
    key_id = (await create(api)).json()["id"]
    assert (await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"limits": limits})).status_code == 422


# ── how long a key may live ──


def lifetime(api, seconds):
    api.app.state.settings.key_max_lifetime_seconds = seconds


async def test_by_default_keys_may_have_no_expiry(api):
    assert (await create(api)).status_code == 201


async def test_when_a_maximum_lifetime_is_set_every_key_needs_an_expiry_within_it(api):
    lifetime(api, 3600)
    assert (await create(api)).status_code == 422
    assert (await create(api, expires_at=int(time.time()) + 7200)).status_code == 422
    assert (await create(api, expires_at=int(time.time()) + 1800)).status_code == 201


async def test_the_maximum_lifetime_also_applies_to_allow_all_keys_and_patches(api):
    lifetime(api, 3600)
    assert (await create(api, allowed_models=[], allow_all=True)).status_code == 422
    key_id = (await create(api, expires_at=int(time.time()) + 600)).json()["id"]
    too_far = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"expires_at": int(time.time()) + 9999})
    cleared = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"expires_at": None})
    assert too_far.status_code == 422 and cleared.status_code == 422
    unrelated = await api.patch(f"/v1/keys/{key_id}", headers=ADMIN_HEADERS, json={"name": "x"})
    assert unrelated.status_code == 200
