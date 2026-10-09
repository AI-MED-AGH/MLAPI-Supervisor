import asyncio
import json
import time

import pytest

from app.lifecycle.queue_scaler import QueueScaler
from app.lifecycle.reaper import IdleReaper
from app.lifecycle.reconciler import Reconciler
from app.lifecycle.wake import WakeConsumer
from app.registry import states
from app.registry.queries import get_model_by_name
from tests.support import _ready_model, get, make_model, deploy

NOW = 1_000_000.0


def reaper(env, now=NOW):
    return IdleReaper(sessionmaker=env.sm, deployer=env.deployer, redis=env.redis,
                      settings=env.settings, clock=lambda: now)


async def set_model(env, **fields):
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        for k, v in fields.items():
            setattr(model, k, v)
        await s.commit()


# ───────────── idle reaper ─────────────
async def test_reaper_sleeps_idle_model(env):
    await _ready_model(env)
    await set_model(env, updated_at=1)
    await env.redis.set("last_active:ecg", int(NOW - 1000))      # default timeout is 900 s
    assert await reaper(env).tick() == ["ecg"]
    assert (await get(env)).state == states.SLEEPING
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "sleeping"


async def test_reaper_keeps_recently_used_model(env):
    await _ready_model(env)
    await set_model(env, updated_at=1)
    await env.redis.set("last_active:ecg", int(NOW - 100))
    assert await reaper(env).tick() == []
    assert (await get(env)).state == states.READY


async def test_reaper_uses_per_model_timeout_and_zero_means_never(env):
    await _ready_model(env)
    await set_model(env, updated_at=1, config={"idle_timeout_s": 50})
    await env.redis.set("last_active:ecg", int(NOW - 100))
    assert await reaper(env).tick() == ["ecg"]
    await set_model(env, state=states.READY, config={"idle_timeout_s": 0})
    assert await reaper(env).tick() == []


async def test_reaper_does_not_sleep_a_model_that_was_just_started(env):
    await _ready_model(env)
    await env.redis.set("last_active:ecg", 5)                     # ancient activity ...
    await set_model(env, updated_at=int(NOW - 10))                # ... but it only became ready 10 s ago
    assert await reaper(env).tick() == []


async def test_reaper_without_any_activity_record_uses_ready_time(env):
    await _ready_model(env)
    await set_model(env, updated_at=int(NOW - 5000))
    assert await reaper(env).tick() == ["ecg"]


async def test_reaper_ignores_non_ready_and_queue_models(env):
    await _ready_model(env)
    await set_model(env, updated_at=1, state=states.DEPLOYING)
    assert await reaper(env).tick() == []
    await set_model(env, state=states.READY, mode="queue")
    assert await reaper(env).tick() == []


# ───────────── wake consumer ─────────────
def waker(env):
    return WakeConsumer(sessionmaker=env.sm, deployer=env.deployer, redis=env.redis)


async def sleeping_model(env):
    await _ready_model(env)
    async with env.sm() as s:
        await env.deployer.sleep(s, await get_model_by_name(s, "ecg"))
        await s.commit()


async def test_wake_request_wakes_sleeping_model(env):
    await sleeping_model(env)
    await env.redis.set("wake_pending:ecg", 1)
    await env.redis.lpush("wake", "ecg")
    assert await waker(env).process_one(timeout=0.1) == "ecg"
    await env.deployer.drain()
    assert (await get(env)).state == states.READY
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "ready"
    assert await env.redis.exists("wake_pending:ecg") == 0


async def test_wake_ignores_unknown_malformed_and_non_sleeping(env):
    await _ready_model(env)
    for name in ("nope", "../x", "ecg\n"):
        await env.redis.lpush("wake", name)
    await env.redis.lpush("wake", "ecg")                          # ready, not sleeping
    for _ in range(4):
        await waker(env).process_one(timeout=0.1)
    await env.deployer.drain()
    assert (await get(env)).state == states.READY
    assert await env.redis.llen("wake") == 0


async def test_wake_with_empty_queue_returns_none(env):
    assert await waker(env).process_one(timeout=0.05) is None


# ───────────── queue scaler ─────────────
def scaler(env, now=NOW, clock=None):
    return QueueScaler(sessionmaker=env.sm, deployer=env.deployer, redis=env.redis,
                       backend=env.backend, settings=env.settings, clock=clock or (lambda: now))


async def worker_alive(env, loaded="true"):
    await env.redis.hset("fastmlapi:ecg:workers:w1", mapping={"model_loaded": loaded, "last_seen": "1"})


async def test_pending_jobs_start_the_worker(env):
    await _ready_model(env, mode="queue")
    assert (await get(env)).state == states.SLEEPING
    await env.redis.lpush("fastmlapi:ecg:pending", "job1")
    await worker_alive(env)
    assert await scaler(env).tick() == ["ecg"]
    await env.deployer.drain()
    assert (await get(env)).state == states.READY
    assert (await env.backend.status("ecg")).worker_replicas == 1
    assert ("scale", "ecg", 1, "worker") in env.backend.calls


async def test_no_jobs_means_worker_stays_down(env):
    await _ready_model(env, mode="queue")
    assert await scaler(env).tick() == []
    assert (await env.backend.status("ecg")).worker_replicas == 0


async def test_worker_that_never_loads_the_model_fails_the_wake(env):
    env.settings.deploy_timeout = 0.2
    await _ready_model(env, mode="queue")
    await env.redis.lpush("fastmlapi:ecg:pending", "job1")
    await worker_alive(env, loaded="false")
    await scaler(env).tick()
    await env.deployer.drain()
    model = await get(env)
    assert model.state == states.FAILED and "worker" in model.state_detail


async def _running_queue_model(env):
    await _ready_model(env, mode="queue")
    await env.backend.scale("ecg", 1, role="worker")
    await set_model(env, state=states.READY, updated_at=1)


async def test_idle_worker_is_stopped_only_after_the_idle_timeout(env):
    await _running_queue_model(env)
    now = [NOW]
    sc = scaler(env, clock=lambda: now[0])
    assert await sc.tick() == []                                  # first empty observation starts the timer
    now[0] += 899
    assert await sc.tick() == []
    now[0] += 2
    assert await sc.tick() == ["ecg"]
    assert (await get(env)).state == states.SLEEPING
    assert (await env.backend.status("ecg")).worker_replicas == 0
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "ready"


async def test_queued_or_inflight_jobs_keep_the_worker_up_and_reset_the_timer(env):
    await _running_queue_model(env)
    now = [NOW]
    sc = scaler(env, clock=lambda: now[0])
    await sc.tick()
    await env.redis.lpush("fastmlapi:ecg:processing:w1", "job1")  # a job is being processed
    now[0] += 5000
    assert await sc.tick() == []
    await env.redis.delete("fastmlapi:ecg:processing:w1")
    assert await sc.tick() == []                                  # work is gone: the timer starts over here
    now[0] += 899
    assert await sc.tick() == []
    now[0] += 2
    assert await sc.tick() == ["ecg"]


async def test_scaler_ignores_sync_models(env):
    await _ready_model(env)
    await env.redis.lpush("fastmlapi:ecg:pending", "job1")
    assert await scaler(env).tick() == []


# ───────────── reconciler ─────────────
def reconciler(env):
    return Reconciler(sessionmaker=env.sm, deployer=env.deployer, redis=env.redis,
                      backend=env.backend, probe=env.probe)


async def test_reconciler_republishes_routes_and_schema_after_redis_wipe(env):
    await _ready_model(env)
    await env.redis.flushall()
    await reconciler(env).tick()
    assert json.loads(await env.redis.get("route:ecg")) == {"url": "http://fake-ecg:8000", "state": "ready", "mode": "sync"}
    assert json.loads(await env.redis.get("schema:ecg"))["model_version"] == "1.0.0"


async def test_reconciler_restores_sleeping_route_state(env):
    await _ready_model(env)
    async with env.sm() as s:
        await env.deployer.sleep(s, await get_model_by_name(s, "ecg"))
        await s.commit()
    await env.redis.delete("route:ecg")
    await reconciler(env).tick()
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "sleeping"


async def test_reconciler_redeploys_a_ready_model_whose_workload_vanished(env):
    await _ready_model(env)
    env.backend.models.clear()
    await reconciler(env).tick()
    await env.deployer.drain()
    assert "ecg" in env.backend.models
    assert (await get(env)).state == states.READY


async def test_reconciler_removes_orphans(env):
    await _ready_model(env)
    from app.cluster.backend import ModelSpec
    await env.backend.apply_model(ModelSpec(name="ghost", image="x@sha256:1", mode="sync", resources=__import__("tests.support", fromlist=["RES"]).RES))
    await env.redis.set("route:phantom", json.dumps({"url": "http://x", "state": "ready", "mode": "sync"}))
    await reconciler(env).tick()
    assert "ghost" not in env.backend.models and "ecg" in env.backend.models
    assert await env.redis.exists("route:phantom") == 0
    assert await env.redis.exists("route:ecg") == 1


async def test_reconciler_cleans_up_removed_models(env):
    await _ready_model(env)
    await set_model(env, state=states.REMOVED)
    await reconciler(env).tick()
    assert await env.redis.exists("route:ecg", "schema:ecg") == 0
    assert "ecg" not in env.backend.models


async def test_reconciler_leaves_busy_models_alone(env):
    await _ready_model(env)
    env.backend.models.clear()
    await set_model(env, state=states.DEPLOYING)
    await reconciler(env).tick()
    await env.deployer.drain()
    assert env.backend.models == {}


# ───────────── workers without a heartbeat (current fastmlapi) and a separate queue Redis ─────────────
async def test_worker_without_any_heartbeat_is_trusted_after_the_grace_period(env):
    env.settings.worker_heartbeat_grace = 0.15
    await _ready_model(env, mode="queue")
    await env.redis.lpush("fastmlapi:ecg:pending", "job1")           # no worker heartbeat key is ever written
    await scaler(env).tick()
    await env.deployer.drain()
    assert (await get(env)).state == states.READY
    assert (await env.backend.status("ecg")).worker_replicas == 1


async def test_a_worker_that_reports_not_loaded_is_not_trusted_even_after_the_grace_period(env):
    env.settings.worker_heartbeat_grace = 0.05
    env.settings.deploy_timeout = 0.3
    await _ready_model(env, mode="queue")
    await env.redis.lpush("fastmlapi:ecg:pending", "job1")
    await worker_alive(env, loaded="false")                          # a heartbeat exists: strict mode
    await scaler(env).tick()
    await env.deployer.drain()
    assert (await get(env)).state == states.FAILED


async def test_queue_data_lives_in_the_queue_redis_not_the_control_redis(env):
    from fakeredis import FakeAsyncRedis

    queue_redis = FakeAsyncRedis(decode_responses=True)
    env.deployer._queue_redis = queue_redis
    await _ready_model(env, mode="queue")
    await queue_redis.lpush("fastmlapi:ecg:pending", "job1")
    await queue_redis.hset("fastmlapi:ecg:workers:w1", mapping={"model_loaded": "true"})
    sc = QueueScaler(sessionmaker=env.sm, deployer=env.deployer, redis=queue_redis, backend=env.backend,
                     settings=env.settings, clock=lambda: NOW)
    assert await sc.tick() == ["ecg"]
    await env.deployer.drain()
    assert (await get(env)).state == states.READY
    assert await env.redis.keys("fastmlapi:*") == []                 # nothing queue-related in the control Redis
    await queue_redis.aclose()
