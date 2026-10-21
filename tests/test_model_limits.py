import json

import pytest

from app.config import Settings
from app.limits import effective_model_limits, route_limits
from app.registry.labels import LabelError, parse_labels
from tests.conftest import ADMIN_HEADERS as H
from tests.test_models_api import IMAGE, LABELS, approve_first, drain, register

CEILINGS = Settings(_env_file=None)  # rpm_per_key 600, rpm_total 6000, concurrency 64, max_pending 10000


# ── what an image can ask for ──


def test_rate_labels_are_read_from_the_image():
    labels = {**LABELS, "mlapi.rate.rpm": "30", "mlapi.rate.total_rpm": "300", "mlapi.rate.concurrency": "4",
              "mlapi.queue.max_pending": "50"}
    assert parse_labels(labels, "ecg").limits == {
        "rpm_per_key": 30, "rpm_total": 300, "max_concurrency": 4, "max_pending": 50,
    }


def test_an_image_without_rate_labels_declares_nothing():
    assert parse_labels(LABELS, "ecg").limits == {}


@pytest.mark.parametrize("value", ["fast", "-1", "1.5", "", "99999999999"])
def test_bad_rate_labels_are_rejected(value):
    with pytest.raises(LabelError):
        parse_labels({**LABELS, "mlapi.rate.rpm": value}, "ecg")


# ── what actually applies ──


def test_a_declared_limit_within_the_ceilings_is_used_as_is():
    got = effective_model_limits(declared={"rpm_per_key": 30, "max_concurrency": 4}, admin={}, mode="sync", settings=CEILINGS)
    assert got == {"rpm_per_key": 30, "max_concurrency": 4}


def test_a_declared_limit_above_the_ceiling_is_lowered_to_it():
    got = effective_model_limits(declared={"rpm_per_key": 5000, "max_concurrency": 1000}, admin={}, mode="sync", settings=CEILINGS)
    assert got == {"rpm_per_key": 600, "max_concurrency": 64}


def test_an_image_cannot_ask_for_unlimited():
    got = effective_model_limits(declared={"rpm_total": 0}, admin={}, mode="sync", settings=CEILINGS)
    assert got == {"rpm_total": 6000}


def test_an_admin_value_wins_over_the_image_and_may_exceed_the_ceiling_or_be_unlimited():
    got = effective_model_limits(
        declared={"rpm_per_key": 30, "rpm_total": 300},
        admin={"rpm_per_key": 5000, "rpm_total": 0},
        mode="sync",
        settings=CEILINGS,
    )
    assert got == {"rpm_per_key": 5000, "rpm_total": 0}


def test_queue_models_always_have_a_pending_cap():
    assert effective_model_limits(declared={}, admin={}, mode="queue", settings=CEILINGS) == {"max_pending": 1000}
    assert effective_model_limits(declared={}, admin={}, mode="sync", settings=CEILINGS) == {}


def test_the_router_is_only_told_what_it_enforces():
    assert route_limits({"rpm_per_key": 1, "rpm_total": 2, "max_concurrency": 3, "max_pending": 4}) == {
        "rpm_per_key": 1, "rpm_total": 2, "max_concurrency": 3,
    }


# ── through the API ──

RATED = {**LABELS, "mlapi.rate.rpm": "30", "mlapi.rate.concurrency": "4"}


async def deploy_rated(api, labels=RATED):
    await register(api, labels=labels)
    await approve_first(api)


async def test_declared_limits_are_stored_and_shown_with_the_effective_ones(api):
    await deploy_rated(api)
    body = (await api.get("/v1/models/ecg", headers=H)).json()
    assert body["declared_limits"] == {"rpm_per_key": 30, "max_concurrency": 4}
    assert body["limits"] == {"rpm_per_key": 30, "max_concurrency": 4}


async def test_the_route_the_router_reads_carries_the_limits(api, redis):
    await deploy_rated(api)
    route = json.loads(await redis.get("route:ecg"))
    assert route["limits"] == {"rpm_per_key": 30, "max_concurrency": 4}
    assert route["state"] == "ready"


async def test_an_admin_changes_limits_without_a_redeploy(api, redis):
    await deploy_rated(api)
    r = await api.patch("/v1/models/ecg/config", headers=H, json={"limits": {"rpm_total": 50, "rpm_per_key": 0}})
    assert r.status_code == 200 and r.json()["redeploy"] is False
    assert json.loads(await redis.get("route:ecg"))["limits"] == {"rpm_per_key": 0, "rpm_total": 50, "max_concurrency": 4}
    assert r.json()["model"]["limits"] == {"rpm_per_key": 0, "rpm_total": 50, "max_concurrency": 4}


async def test_clearing_the_admin_override_goes_back_to_what_the_image_declared(api, redis):
    await deploy_rated(api)
    await api.patch("/v1/models/ecg/config", headers=H, json={"limits": {"rpm_per_key": 0}})
    await api.patch("/v1/models/ecg/config", headers=H, json={"limits": {}})
    assert json.loads(await redis.get("route:ecg"))["limits"] == {"rpm_per_key": 30, "max_concurrency": 4}


@pytest.mark.parametrize("limits", [{"nope": 1}, {"rpm_total": -1}, {"rpm_total": "9"}, {"rpm_total": True}, [], 5])
async def test_bad_limits_are_rejected_and_change_nothing(api, redis, limits):
    await deploy_rated(api)
    before = await redis.get("route:ecg")
    r = await api.patch("/v1/models/ecg/config", headers=H, json={"limits": limits})
    assert r.status_code == 422
    assert await redis.get("route:ecg") == before


async def test_limits_above_the_ceiling_in_an_image_are_lowered_in_the_route(api, redis):
    await deploy_rated(api, {**LABELS, "mlapi.rate.rpm": "100000"})
    assert json.loads(await redis.get("route:ecg"))["limits"] == {"rpm_per_key": 600}


async def test_changing_the_pending_cap_of_a_queue_model_redeploys_it(api):
    from tests.support import FakeQueueAccess

    api.app.state.queue_access = FakeQueueAccess()
    await deploy_rated(api, {**LABELS, "mlapi.mode": "queue"})
    assert api.backend.models["ecg"].spec.max_pending == 1000  # every queue has a cap, even if nobody chose one
    unrelated = await api.patch("/v1/models/ecg/config", headers=H, json={"limits": {"rpm_total": 9}})
    assert unrelated.json()["redeploy"] is False
    await drain(api)
    changed = await api.patch("/v1/models/ecg/config", headers=H, json={"limits": {"max_pending": 20}})
    assert changed.json()["redeploy"] is True
    await drain(api)
    assert api.backend.models["ecg"].spec.max_pending == 20


# ── the route index the Router lists models from ──


async def test_deploying_adds_to_the_route_index_and_removing_takes_it_out(api, redis):
    await deploy_rated(api)
    assert await redis.smembers("route_index") == {"ecg"}
    await api.delete("/v1/models/ecg", headers=H)
    await drain(api)
    assert await redis.smembers("route_index") == set()
    assert await redis.exists("route:ecg") == 0


async def test_the_index_is_built_from_existing_routes_at_startup(redis):
    from app.redis_sync.routes import publish_route, rebuild_route_index

    await publish_route(redis, "a", url="http://a", state="ready", mode="sync")
    await redis.delete("route_index", "route_index_ready")  # a Supervisor from before the index existed
    await publish_route(redis, "b", url="http://b", state="ready", mode="sync")
    await redis.srem("route_index", "b")
    await rebuild_route_index(redis)
    assert await redis.smembers("route_index") == {"a", "b"}
    assert await redis.get("route_index_ready") == "1"


async def test_the_reconciler_restores_a_wiped_route_with_its_limits(api, redis):
    from app.lifecycle.reconciler import Reconciler

    await deploy_rated(api)
    await redis.delete("route:ecg", "route_index")
    services = api.app.state.services
    await Reconciler(
        sessionmaker=api.app.state.sessionmaker, deployer=services.deployer, redis=redis,
        backend=services.backend, probe=services.probe, settings=services.deployer.settings,
    ).tick()
    assert json.loads(await redis.get("route:ecg"))["limits"] == {"rpm_per_key": 30, "max_concurrency": 4}
    assert await redis.smembers("route_index") == {"ecg"}
