import json


async def publish_route(redis, name: str, *, url: str, state: str, mode: str) -> None:
    await redis.set(f"route:{name}", json.dumps({"url": url, "state": state, "mode": mode}))


async def delete_route(redis, name: str) -> None:
    await redis.delete(f"route:{name}")


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
