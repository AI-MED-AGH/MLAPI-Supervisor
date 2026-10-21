import time

import pytest

from app.cluster.backend import BackendError, Usage
from app.telemetry import store
from app.telemetry.collector import UsageCollector
from app.telemetry.tiers import RANGES, TIERS
from tests.conftest import ADMIN_HEADERS
from tests.support import _ready_model, build_env, make_model

T0 = 1_000_000_020  # a multiple of 5, 60 and 20; the tests build on whole minutes from it
MIN0 = T0 - T0 % 60
HOUR0 = T0 - T0 % 3600
DAY0 = T0 - T0 % 86400
GI = 1024**3


async def rows(session, model="ecg", tier="5s"):
    return [(r.ts, r.cpu_m_avg, r.cpu_m_max, r.mem_avg, r.mem_max, r.n) for r in await store.raw_rows(session, model, tier)]


# ───────────── tiers ─────────────

def test_tiers_match_the_product_spec():
    assert [(t.name, t.step, t.keep) for t in TIERS] == [
        ("5s", 5, 3600), ("1m", 60, 86400), ("1h", 3600, 30 * 86400), ("1d", 86400, 365 * 86400)]
    assert RANGES == {"1h": "5s", "24h": "1m", "30d": "1h", "1y": "1d"}


# ───────────── store ─────────────

async def test_record_snaps_to_five_second_buckets_and_overwrites_within_one(session):
    await store.record(session, "ecg", Usage(cpu_m=100, memory_bytes=GI), now=T0 + 1)
    await store.record(session, "ecg", Usage(cpu_m=300, memory_bytes=2 * GI), now=T0 + 4)
    await store.record(session, "ecg", Usage(cpu_m=50, memory_bytes=GI), now=T0 + 5)
    got = await rows(session)
    assert got == [(T0, 300, 300, 2 * GI, 2 * GI, 1), (T0 + 5, 50, 50, GI, GI, 1)]


async def test_minute_rollup_averages_and_keeps_the_peak_and_skips_the_open_minute(session):
    for i in range(12):  # a full minute of 5 s samples, cpu 100, 200, ... 1200
        await store.record(session, "ecg", Usage(cpu_m=100 * (i + 1), memory_bytes=GI * (i + 1)), now=MIN0 + 5 * i)
    await store.record(session, "ecg", Usage(cpu_m=7, memory_bytes=1), now=MIN0 + 60)  # the next, still open minute
    await store.rollup(session, now=MIN0 + 61)
    assert await rows(session, tier="1m") == [(MIN0, 650.0, 1200.0, GI * 6.5, GI * 12, 12)]


async def test_rollup_runs_once_per_bucket_and_is_idempotent(session):
    await store.record(session, "ecg", Usage(cpu_m=10, memory_bytes=1), now=MIN0)
    await store.rollup(session, now=MIN0 + 60)
    await store.rollup(session, now=MIN0 + 90)
    assert len(await rows(session, tier="1m")) == 1


async def test_rollup_chains_minute_hour_day_weighting_by_sample_count(session):
    # hour 0: one busy minute (12 samples at 600) and one quiet minute (3 samples at 0)
    for i in range(12):
        await store.record(session, "ecg", Usage(cpu_m=600, memory_bytes=GI), now=HOUR0 + 5 * i)
    for i in range(3):
        await store.record(session, "ecg", Usage(cpu_m=0, memory_bytes=GI), now=HOUR0 + 60 + 5 * i)
    await store.rollup(session, now=HOUR0 + 3600 + 1)
    [(ts, avg, peak, mem_avg, mem_peak, n)] = await rows(session, tier="1h")
    assert (ts, peak, n) == (HOUR0, 600, 15)
    assert avg == pytest.approx(600 * 12 / 15)
    # the hour closes the day only when the whole day is over
    assert await rows(session, tier="1d") == []
    await store.rollup(session, now=HOUR0 - HOUR0 % 86400 + 86400 + 1)
    [(day_ts, day_avg, *_)] = await rows(session, tier="1d")
    assert day_ts == HOUR0 - HOUR0 % 86400 and day_avg == pytest.approx(600 * 12 / 15)


async def test_models_are_rolled_up_separately(session):
    await store.record(session, "a", Usage(cpu_m=100, memory_bytes=1), now=MIN0)
    await store.record(session, "b", Usage(cpu_m=900, memory_bytes=1), now=MIN0)
    await store.rollup(session, now=MIN0 + 60)
    assert (await rows(session, "a", "1m"))[0][1] == 100 and (await rows(session, "b", "1m"))[0][1] == 900


async def test_prune_drops_each_tier_after_its_own_retention(session):
    now = MIN0 + 10 * 86400
    await store.record(session, "ecg", Usage(cpu_m=1, memory_bytes=1), now=now - 3601)   # raw: kept 1 h
    await store.record(session, "ecg", Usage(cpu_m=2, memory_bytes=1), now=now - 3000)
    await store.rollup(session, now=now)
    await store.prune(session, now=now)
    assert [r[0] for r in await rows(session)] == [(now - 3000) // 5 * 5]
    assert len(await rows(session, tier="1m")) >= 1                                      # rollups outlive their raw rows


async def test_series_returns_the_tier_for_the_range_inside_its_window(session):
    now = T0 + 7200
    await store.record(session, "ecg", Usage(cpu_m=100, memory_bytes=GI), now=now - 4000)  # outside 1 h
    await store.record(session, "ecg", Usage(cpu_m=250, memory_bytes=GI), now=now - 10)
    points = await store.series(session, "ecg", "1h", now=now)
    assert points == [{"t": (now - 10) // 5 * 5, "cpu_m": 250, "cpu_m_max": 250, "memory_bytes": GI, "memory_bytes_max": GI}]
    with pytest.raises(ValueError):
        await store.series(session, "ecg", "2h", now=now)


async def test_latest_is_the_newest_live_sample_and_goes_stale(session):
    assert await store.latest(session, "ecg", now=T0) is None
    await store.record(session, "ecg", Usage(cpu_m=10, memory_bytes=1), now=T0)
    await store.record(session, "ecg", Usage(cpu_m=20, memory_bytes=2), now=T0 + 5)
    assert await store.latest(session, "ecg", now=T0 + 10) == {"t": T0 + 5, "cpu_m": 20, "memory_bytes": 2}
    assert await store.latest(session, "ecg", now=T0 + 100) is None


async def test_delete_model_removes_all_its_tiers_only(session):
    await store.record(session, "a", Usage(cpu_m=1, memory_bytes=1), now=MIN0)
    await store.record(session, "b", Usage(cpu_m=1, memory_bytes=1), now=MIN0)
    await store.rollup(session, now=MIN0 + 60)
    await store.delete_model(session, "a")
    assert await rows(session, "a") == [] and await rows(session, "a", "1m") == []
    assert len(await rows(session, "b")) == 1


# ───────────── collector ─────────────

@pytest.fixture
def env(sessionmaker, redis):
    return build_env(sessionmaker, redis)


def collector(env, now):
    return UsageCollector(sessionmaker=env.sm, backend=env.backend, clock=lambda: now)


async def test_collector_records_usage_of_running_models_only(env):
    await _ready_model(env)                                           # ecg: ready
    await make_model(env, name="idle", digest="sha256:b")             # idle: approved, never deployed
    env.backend.usage_by_model = {"ecg": Usage(cpu_m=420, memory_bytes=GI), "idle": Usage(cpu_m=1, memory_bytes=1)}
    await collector(env, T0).tick()
    async with env.sm() as s:
        assert [r[1] for r in await rows(s)] == [420]
        assert await rows(s, "idle") == []


async def test_collector_skips_models_with_no_running_workload_and_survives_backend_errors(env):
    await _ready_model(env)
    env.backend.usage_by_model = {"ecg": None}
    await collector(env, T0).tick()
    env.backend.usage_error = BackendError("docker is down")
    await collector(env, T0 + 5).tick()                               # must not raise
    async with env.sm() as s:
        assert await rows(s) == []


async def test_collector_rolls_up_closed_buckets(env):
    await _ready_model(env)
    env.backend.usage_by_model = {"ecg": Usage(cpu_m=200, memory_bytes=GI)}
    for i in range(13):
        await collector(env, MIN0 + 5 * i).tick()
    async with env.sm() as s:
        [(ts, avg, peak, _, _, n)] = await rows(s, tier="1m")
        assert (ts, avg, peak, n) == (MIN0, 200, 200, 12)


async def test_removed_models_are_not_sampled_and_their_history_is_dropped(env):
    mid = await _ready_model(env)
    env.backend.usage_by_model = {"ecg": Usage(cpu_m=1, memory_bytes=1)}
    await collector(env, T0).tick()
    async with env.sm() as s:
        from app.registry.queries import get_model
        await env.deployer.remove(s, await get_model(s, mid))
        await s.commit()
    await collector(env, T0 + 5).tick()
    async with env.sm() as s:
        assert await rows(s) == []


# ───────────── API ─────────────

async def seed_model(api):
    from tests.test_models_api import register

    return await register(api)


async def test_metrics_endpoint_requires_admin_and_a_known_model(api):
    assert (await api.get("/v1/models/ecg/metrics")).status_code == 401
    assert (await api.get("/v1/models/ghost/metrics", headers=ADMIN_HEADERS)).status_code == 404


async def test_metrics_endpoint_returns_points_limits_and_step(api, sessionmaker):
    await seed_model(api)
    async with sessionmaker() as s:
        await store.record(s, "ecg", Usage(cpu_m=300, memory_bytes=GI), now=time.time() - 20)
        await s.commit()
    r = await api.get("/v1/models/ecg/metrics?range=1h", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["range"] == "1h" and body["step_seconds"] == 5
    assert body["points"][0]["cpu_m"] == 300 and body["points"][0]["memory_bytes"] == GI
    assert body["latest"]["cpu_m"] == 300 and body["latest"]["t"] == body["points"][0]["t"]  # 20 s old: still live
    assert set(body["limits"]) == {"cpu_m", "memory_bytes"}
    assert (await api.get("/v1/models/ecg/metrics?range=24h", headers=ADMIN_HEADERS)).json()["step_seconds"] == 60


async def test_metrics_endpoint_rejects_unknown_ranges(api):
    await seed_model(api)
    assert (await api.get("/v1/models/ecg/metrics?range=5y", headers=ADMIN_HEADERS)).status_code == 422
