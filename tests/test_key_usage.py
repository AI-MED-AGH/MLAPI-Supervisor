import time

from tests.conftest import ADMIN_HEADERS as H


def day(offset: int = 0) -> str:
    return time.strftime("%Y%m%d", time.gmtime(time.time() - offset * 86400))


async def make_key(api):
    r = await api.post("/v1/keys", headers=H, json={"name": "ci", "allowed_models": ["m1"]})
    return r.json()["id"]


async def test_usage_needs_an_admin(api):
    assert (await api.get("/v1/keys/abc/usage")).status_code == 401


async def test_usage_of_an_unknown_key_is_404(api):
    assert (await api.get("/v1/keys/nope/usage", headers=H)).status_code == 404


async def test_a_key_that_was_never_used_has_empty_usage(api):
    key_id = await make_key(api)
    r = await api.get(f"/v1/keys/{key_id}/usage", headers=H)
    assert r.status_code == 200
    assert r.json() == {"key": key_id, "last_used": None, "days": []}


async def test_usage_is_reported_per_day_and_model_newest_first(api, redis):
    key_id = await make_key(api)
    await redis.hset(f"usage:{day(0)}:{key_id}", mapping={
        "m1:requests": 10, "m1:errors": 1, "m1:limited": 2, "-:requests": 3, "-:limited": 3, "last_used": 2000,
    })
    await redis.hset(f"usage:{day(1)}:{key_id}", mapping={"m2:requests": 5, "last_used": 1000})
    body = (await api.get(f"/v1/keys/{key_id}/usage?days=3", headers=H)).json()
    assert body["last_used"] == 2000
    assert [d["day"] for d in body["days"]] == [day(0), day(1)]
    assert body["days"][0]["models"] == {
        "m1": {"requests": 10, "errors": 1, "limited": 2},
        "-": {"requests": 3, "errors": 0, "limited": 3},
    }
    assert body["days"][1]["models"] == {"m2": {"requests": 5, "errors": 0, "limited": 0}}


async def test_only_the_asked_for_days_are_returned(api, redis):
    key_id = await make_key(api)
    await redis.hset(f"usage:{day(5)}:{key_id}", mapping={"m1:requests": 1, "last_used": 1})
    assert (await api.get(f"/v1/keys/{key_id}/usage?days=2", headers=H)).json()["days"] == []
    assert len((await api.get(f"/v1/keys/{key_id}/usage?days=7", headers=H)).json()["days"]) == 1


async def test_the_range_is_bounded(api):
    key_id = await make_key(api)
    for days in ("0", "36", "x"):
        assert (await api.get(f"/v1/keys/{key_id}/usage?days={days}", headers=H)).status_code == 422


async def test_a_revoked_keys_history_can_still_be_read(api, redis):
    key_id = await make_key(api)
    await redis.hset(f"usage:{day(0)}:{key_id}", mapping={"m1:requests": 4, "last_used": 7})
    await api.delete(f"/v1/keys/{key_id}", headers=H)
    r = await api.get(f"/v1/keys/{key_id}/usage", headers=H)
    assert r.status_code == 200 and r.json()["days"][0]["models"]["m1"]["requests"] == 4
