"""Attacks the per-model Redis ACL on a REAL Redis. Opt in with REAL_REDIS_URL=redis://:<admin password>@host:port/0
(use a throwaway instance: it is flushed). Needs Redis >= 7. Example:
    docker run -d --rm -p 127.0.0.1:16379:6379 redis:7.4-alpine redis-server --requirepass adminpw
    REAL_REDIS_URL=redis://:adminpw@127.0.0.1:16379/0 pytest tests/test_acl_real_redis.py

Two modes: strict (the default: what the current fastmlapi needs, no scripts and no SCAN) and legacy (QUEUE_LEGACY_ACL, for
older fastmlapi versions that used Lua and SCAN).
"""
import os
from urllib.parse import urlsplit

import pytest
import redis.asyncio as aioredis
from redis.exceptions import NoPermissionError, ResponseError

from app.queue_access.acl import QueueAccess

URL = os.environ.get("REAL_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set REAL_REDIS_URL to run against a real Redis")


@pytest.fixture(params=["strict", "legacy"])
async def world(request):
    admin = aioredis.from_url(URL, decode_responses=True)
    await admin.flushall()
    await admin.set("route:other-model", "secret-routing-data")
    await admin.set("key:abcdefghijkl", "secret-key-hash")
    await admin.lpush("fastmlapi:other:pending", "someone-elses-job")
    parts = urlsplit(URL)
    qa = QueueAccess(admin, secret="real-redis-test-secret", model_redis_url=f"redis://{parts.hostname}:{parts.port}/0",
                     legacy=request.param == "legacy")
    await qa.verify_server()
    url = await qa.provision("ecg")
    model = aioredis.from_url(url, decode_responses=True)
    yield request.param, admin, qa, model
    await model.aclose()
    await admin.flushall()
    await admin.aclose()


async def denied(coro):
    with pytest.raises((NoPermissionError, ResponseError)):
        await coro


# ───────────── both modes ─────────────
async def test_the_models_own_namespace_works_with_transactions(world):
    """Everything the current fastmlapi RedisBackend does: LMOVE claim, WATCH/MULTI/EXEC updates, sets, hashes, TIME."""
    _, _, _, m = world
    await m.lpush("fastmlapi:ecg:pending", "j1")
    assert await m.lmove("fastmlapi:ecg:pending", "fastmlapi:ecg:processing:w1", "RIGHT", "LEFT") == "j1"
    assert await m.lpos("fastmlapi:ecg:processing:w1", "j1") == 0
    async with m.pipeline(transaction=True) as pipe:                                  # WATCH ... MULTI ... EXEC
        await pipe.watch("fastmlapi:ecg:job:j1")
        pipe.multi()
        pipe.sadd("fastmlapi:ecg:worker_index", "w1")
        pipe.hset("fastmlapi:ecg:job:j1", mapping={"status": "running"})
        pipe.hsetnx("fastmlapi:ecg:workers:w1", "started_at", "1")
        pipe.hincrby("fastmlapi:ecg:job:j1", "attempts", 1)
        pipe.expire("fastmlapi:ecg:workers:w1", 30)
        await pipe.execute()
    assert await m.smembers("fastmlapi:ecg:worker_index") == {"w1"}
    assert await m.hmget("fastmlapi:ecg:job:j1", "status", "attempts") == ["running", "1"]
    assert await m.lrem("fastmlapi:ecg:processing:w1", 0, "j1") == 1
    assert await m.time()


async def test_other_keys_are_unreachable(world):
    _, admin, _, m = world
    await denied(m.get("route:other-model"))
    await denied(m.get("key:abcdefghijkl"))
    await denied(m.lrange("fastmlapi:other:pending", 0, -1))
    await denied(m.lpush("fastmlapi:other:pending", "evil"))
    await denied(m.set("route:ecg", "http://evil"))
    await denied(m.lmove("fastmlapi:ecg:pending", "route:stolen", "RIGHT", "LEFT"))
    assert await admin.get("route:other-model") == "secret-routing-data"
    assert await admin.llen("fastmlapi:other:pending") == 1


@pytest.mark.parametrize(
    "command",
    [("FLUSHALL",), ("FLUSHDB",), ("SWAPDB", "0", "1"), ("KEYS", "*"), ("DBSIZE",), ("INFO",), ("CONFIG", "GET", "*"),
     ("CLIENT", "LIST"), ("SHUTDOWN", "NOSAVE"), ("MONITOR",), ("ACL", "SETUSER", "m_ecg", "+@all", "~*"),
     ("PUBLISH", "wake", "x"), ("EVAL", "return 1", "0"), ("SCRIPT", "FLUSH"), ("SCRIPT", "KILL"), ("SELECT", "1"),
     ("RENAME", "fastmlapi:ecg:pending", "route:x"), ("COPY", "fastmlapi:ecg:pending", "route:y"), ("RANDOMKEY",)],
)
async def test_dangerous_commands_are_refused(world, command):
    _, admin, _, m = world
    await denied(m.execute_command(*command))
    assert await admin.exists("route:other-model", "key:abcdefghijkl") == 2


async def test_provisioning_is_repeatable_and_removal_locks_the_user_out(world):
    _, _, qa, m = world
    first = await qa.provision("ecg")
    assert await qa.provision("ecg") == first
    await m.ping()
    await qa.remove("ecg")
    with pytest.raises(Exception):
        await aioredis.from_url(first, decode_responses=True).ping()


# ───────────── strict (default): no scripting, no SCAN ─────────────
async def test_strict_users_cannot_script_or_scan(world):
    mode, admin, _, m = world
    if mode != "strict":
        pytest.skip("legacy mode allows these on purpose")
    await m.lpush("fastmlapi:ecg:pending", "j1")
    await denied(m.scan(0))
    await denied(m.register_script("return 1")())                          # EVALSHA / SCRIPT LOAD
    await denied(m.execute_command("SCRIPT", "LOAD", "return 1"))
    assert await admin.exists("route:other-model") == 1


# ───────────── legacy: scripts and SCAN, with their documented limits ─────────────
async def test_legacy_scan_lists_key_names_but_never_their_values(world):
    """KNOWN LIMITATION of legacy mode, pinned on purpose: SCAN is not filtered by key permissions."""
    mode, admin, _, m = world
    if mode != "legacy":
        pytest.skip("strict mode cannot SCAN")
    await m.lpush("fastmlapi:ecg:pending", "j1")
    seen = {k async for k in m.scan_iter(match="*", count=1000)}
    assert "fastmlapi:ecg:pending" in seen and "route:other-model" in seen
    await denied(m.get("route:other-model"))
    assert await admin.get("route:other-model") == "secret-routing-data"


async def test_legacy_scripts_still_obey_the_key_prefix_and_command_list(world):
    mode, admin, _, m = world
    if mode != "legacy":
        pytest.skip("strict mode cannot run scripts")
    await m.register_script("return redis.call('HSET', KEYS[1], 'status', ARGV[1])")(keys=["fastmlapi:ecg:job:j1"], args=["running"])
    for body in ("return redis.call('GET', 'route:other-model')", "return redis.call('RPOP', 'fastmlapi:other:pending')",
                 "return redis.call('SET', 'route:ecg', 'http://evil')", "return redis.call('KEYS', '*')",
                 "return redis.call('FLUSHALL')"):
        await denied(m.register_script(body)())
    assert await admin.llen("fastmlapi:other:pending") == 1 and await admin.exists("route:ecg") == 0


# ───────────── strict: nothing expensive or off-library, even on the model's own keys ─────────────
@pytest.mark.parametrize(
    "command",
    [("SET", "fastmlapi:ecg:s", "x"), ("GET", "fastmlapi:ecg:s"), ("APPEND", "fastmlapi:ecg:s", "x"),
     ("SETRANGE", "fastmlapi:ecg:s", "100000000", "x"), ("LCS", "fastmlapi:ecg:a", "fastmlapi:ecg:b"),
     ("SUNIONSTORE", "fastmlapi:ecg:dst", "fastmlapi:ecg:a", "fastmlapi:ecg:b"), ("SINTER", "fastmlapi:ecg:a", "fastmlapi:ecg:b"),
     ("ZADD", "fastmlapi:ecg:z", "1", "a"), ("SORT", "fastmlapi:ecg:l"), ("PFADD", "fastmlapi:ecg:p", "a"),
     ("LSET", "fastmlapi:ecg:l", "0", "x"), ("LINSERT", "fastmlapi:ecg:l", "BEFORE", "a", "b"), ("LTRIM", "fastmlapi:ecg:l", "0", "1"),
     ("RPOP", "fastmlapi:ecg:l"), ("UNLINK", "fastmlapi:ecg:l"), ("PEXPIRE", "fastmlapi:ecg:l", "1000"),
     ("HSCAN", "fastmlapi:ecg:h", "0"), ("SSCAN", "fastmlapi:ecg:s", "0"), ("OBJECT", "ENCODING", "fastmlapi:ecg:l")],
)
async def test_strict_users_can_only_run_the_commands_the_library_uses(world, command):
    mode, _, _, m = world
    if mode != "strict":
        pytest.skip("legacy mode keeps the data-structure categories for older libraries")
    await denied(m.execute_command(*command))
