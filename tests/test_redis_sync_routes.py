import json

from app.redis_sync.routes import delete_route, delete_schema, publish_route, publish_schema


async def test_route_matches_router_contract(redis):
    await publish_route(redis, "ecg", url="http://m-ecg:8000", state="ready", mode="sync")
    assert json.loads(await redis.get("route:ecg")) == {
        "url": "http://m-ecg:8000", "state": "ready", "mode": "sync",
    }


async def test_route_state_update_overwrites(redis):
    await publish_route(redis, "ecg", url="http://m-ecg:8000", state="ready", mode="queue")
    await publish_route(redis, "ecg", url="http://m-ecg:8000", state="sleeping", mode="queue")
    assert json.loads(await redis.get("route:ecg"))["state"] == "sleeping"


async def test_delete_route_and_schema(redis):
    await publish_route(redis, "ecg", url="http://x", state="ready", mode="sync")
    await publish_schema(redis, "ecg", {"model_version": "1"})
    await delete_route(redis, "ecg")
    await delete_schema(redis, "ecg")
    assert await redis.exists("route:ecg", "schema:ecg") == 0


async def test_schema_keeps_only_contract_fields(redis):
    await publish_schema(redis, "ecg", {
        "model_name": "ecg", "model_version": "1.2.0",
        "request_schema": {"type": "object"}, "response_schema": {"type": "object"},
        "example": {"data": 1}, "internal_secret": "nope",
    })
    stored = json.loads(await redis.get("schema:ecg"))
    assert stored == {
        "request_schema": {"type": "object"}, "response_schema": {"type": "object"},
        "example": {"data": 1}, "model_version": "1.2.0",
    }


async def test_schema_with_missing_fields_defaults_to_none(redis):
    await publish_schema(redis, "ecg", {})
    assert json.loads(await redis.get("schema:ecg")) == {
        "request_schema": None, "response_schema": None, "example": None, "model_version": None,
    }
