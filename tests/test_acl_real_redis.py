"""Attacks the per-model Redis ACL on a REAL Redis. Opt in with REAL_REDIS_URL=redis://:<admin password>@host:port/0
(use a throwaway instance: it is flushed). Needs Redis >= 7. Example:
    docker run -d --rm -p 127.0.0.1:16379:6379 redis:7.4-alpine redis-server --requirepass adminpw
    REAL_REDIS_URL=redis://:adminpw@127.0.0.1:16379/0 pytest tests/test_acl_real_redis.py
"""
import os

import pytest
import redis.asyncio as aioredis
from redis.exceptions import NoPermissionError, ResponseError

from app.queue_access.acl import QueueAccess

URL = os.environ.get("REAL_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set REAL_REDIS_URL to run against a real Redis")


@pytest.fixture
async def world():
    admin = aioredis.from_url(URL, decode_responses=True)
    await admin.flushall()
    await admin.set("route:other-model", "secret-routing-data")
    await admin.set("key:abcdefghijkl", "secret-key-hash")
    await admin.lpush("fastmlapi:other:pending", "someone-elses-job")
    qa = QueueAccess(admin, secret="real-redis-test-secret", model_redis_url=URL.split("@")[-1].join(["redis://", ""]))
    await qa.verify_server()
    url = await qa.provision("ecg")
    model = aioredis.from_url(url, decode_responses=True)
    yield admin, qa, model
    await model.aclose()
    await admin.flushall()
    await admin.aclose()


async def denied(coro):
    with pytest.raises((NoPermissionError, ResponseError)):
        await coro


async def test_the_models_own_namespace_works_including_scripts_and_transactions(world):
    _, _, m = world
    await m.lpush("fastmlapi:ecg:pending", "j1")
    assert await m.blmove("fastmlapi:ecg:pending", "fastmlapi:ecg:processing:w1", 1, "RIGHT", "LEFT") == "j1"
    script = m.register_script("return redis.call('HSET', KEYS[1], 'status', ARGV[1])")      # EVALSHA + SCRIPT LOAD
    await script(keys=["fastmlapi:ecg:job:j1"], args=["running"])
    pipe = m.pipeline(transaction=True)                                                      # MULTI / EXEC
    pipe.hset("fastmlapi:ecg:job:j1", "attempts", 1)
    pipe.lpush("fastmlapi:ecg:pending", "j2")
    await pipe.execute()
    assert await m.hgetall("fastmlapi:ecg:job:j1") == {"status": "running", "attempts": "1"}
    assert await m.time()


async def test_scan_lists_key_names_but_never_their_values(world):
    """KNOWN LIMITATION, pinned on purpose: SCAN is not filtered by key permissions, so a model can see the NAMES of all keys
    in its Redis. That is why queue models must get a dedicated Redis (nothing but queue data in it). Values stay protected."""
    admin, _, m = world
    await m.lpush("fastmlapi:ecg:pending", "j1")
    seen = {k async for k in m.scan_iter(match="*", count=1000)}
    assert "fastmlapi:ecg:pending" in seen and "route:other-model" in seen          # names leak ...
    await denied(m.get("route:other-model"))                                         # ... values and contents do not
    await denied(m.lrange("fastmlapi:other:pending", 0, -1))
    assert await admin.get("route:other-model") == "secret-routing-data"


async def test_other_keys_are_unreachable_directly_and_from_inside_scripts(world):
    admin, _, m = world
    await denied(m.get("route:other-model"))
    await denied(m.lrange("fastmlapi:other:pending", 0, -1))
    await denied(m.set("route:ecg", "http://evil"))
    for body in ("return redis.call('GET', 'route:other-model')",
                 "return redis.call('RPOP', 'fastmlapi:other:pending')",
                 "return redis.call('SET', 'route:ecg', 'http://evil')",
                 "return redis.call('KEYS', '*')",
                 "return redis.call('FLUSHALL')"):
        await denied(m.register_script(body)())
    assert await admin.get("route:other-model") == "secret-routing-data"
    assert await admin.llen("fastmlapi:other:pending") == 1
    assert await admin.exists("route:ecg") == 0


@pytest.mark.parametrize(
    "command",
    [("FLUSHALL",), ("FLUSHDB",), ("SWAPDB", "0", "1"), ("KEYS", "*"), ("DBSIZE",), ("INFO",), ("CONFIG", "GET", "*"),
     ("CLIENT", "LIST"), ("SHUTDOWN", "NOSAVE"), ("MONITOR",), ("ACL", "SETUSER", "m_ecg", "+@all", "~*"),
     ("PUBLISH", "wake", "x"), ("EVAL", "return 1", "0"), ("SCRIPT", "FLUSH"), ("SCRIPT", "KILL"), ("SELECT", "1"),
     ("RENAME", "fastmlapi:ecg:pending", "route:x"), ("COPY", "fastmlapi:ecg:pending", "route:y")],
)
async def test_dangerous_commands_are_refused(world, command):
    admin, _, m = world
    await denied(m.execute_command(*command))
    assert await admin.exists("route:other-model", "key:abcdefghijkl") == 2


async def test_provisioning_is_repeatable_and_removal_locks_the_user_out(world):
    _, qa, m = world
    first = await qa.provision("ecg")
    assert await qa.provision("ecg") == first
    await m.ping()
    await qa.remove("ecg")
    with pytest.raises(Exception):
        await aioredis.from_url(first, decode_responses=True).ping()
