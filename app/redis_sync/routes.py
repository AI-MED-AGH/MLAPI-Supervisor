import json

ROUTE_INDEX = "route_index"  # the names of all routes: lets the Router list models without scanning every key in Redis
ROUTE_INDEX_READY = "route_index_ready"  # set once the index is complete; until then the Router falls back to scanning


async def publish_route(redis, name: str, *, url: str, state: str, mode: str, limits: dict | None = None) -> None:
    record = {"url": url, "state": state, "mode": mode}
    if limits:
        record["limits"] = limits
    await redis.set(f"route:{name}", json.dumps(record))
    await redis.sadd(ROUTE_INDEX, name)


async def delete_route(redis, name: str) -> None:
    await redis.delete(f"route:{name}")
    await redis.srem(ROUTE_INDEX, name)


async def rebuild_route_index(redis) -> None:
    """Makes the index match the routes that exist, then tells the Router it can rely on it."""
    names = {key.removeprefix("route:") async for key in redis.scan_iter(match="route:*")}
    stale = set(await redis.smembers(ROUTE_INDEX)) - names
    if stale:
        await redis.srem(ROUTE_INDEX, *stale)
    if names:
        await redis.sadd(ROUTE_INDEX, *names)
    await redis.set(ROUTE_INDEX_READY, "1")


async def publish_schema(redis, name: str, schema: dict) -> None:
    """Stores only the fields the Router exposes (fastmlapi's /schema has more)."""
    record = {
        "request_schema": schema.get("request_schema"),
        "response_schema": schema.get("response_schema"),
        "example": schema.get("example"),
        "model_version": schema.get("model_version"),
    }
    await redis.set(f"schema:{name}", json.dumps(record))


async def delete_schema(redis, name: str) -> None:
    await redis.delete(f"schema:{name}")
