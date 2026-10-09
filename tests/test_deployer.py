import asyncio
import json

import pytest

from app.cluster.backend import BackendError
from app.cluster.fake import FakeBackend
from app.config import Settings
from app.deployer.service import Deployer
from app.events import EventBus
from app.registry import states
from app.registry.errors import Conflict, Invalid
from app.registry.labels import ModelLabels
from app.registry.queries import get_model_by_name, list_deployments
from app.registry.resources import Resources
from app.registry.service import RegistryService
from tests.fakes import FakeProbe

GI = 1024**3
RES = Resources(cpu_m=1000, memory_bytes=2 * GI, gpu=False, disk_bytes=5 * GI)
GPU_RES = Resources(cpu_m=1000, memory_bytes=2 * GI, gpu=True, disk_bytes=5 * GI)


class Notifier:
    def __init__(self):
        self.events = []

    async def notify(self, session, event, payload):
        self.events.append((event, payload["model"], payload["digest"], payload["details"]))

    def names(self):
        return [e[0] for e in self.events]


class Env:
    pass


@pytest.fixture
def env(sessionmaker, redis):
    e = Env()
    e.settings = Settings(_env_file=None, cluster_backend="fake", deploy_timeout=1.5,
                          deploy_poll_interval=0.01, gpu_wait_max=5, auto_migrate=False)
    e.backend, e.probe, e.redis, e.sm = FakeBackend(), FakeProbe(), redis, sessionmaker
    e.notifier = Notifier()
    e.events = EventBus(sessionmaker, e.notifier)
    e.registry = RegistryService(e.events)
    e.deployer = Deployer(sessionmaker=sessionmaker, backend=e.backend, redis=redis,
                          events=e.events, probe=e.probe, settings=e.settings)
    return e


async def make_model(env, *, mode="sync", resources=RES, digest="sha256:a", name="ecg"):
    """Registers and approves a model; returns its id (not yet deployed)."""
    async with env.sm() as s:
        sub = await env.registry.submit_digest(
            s, name=name, image=f"ghcr.io/org/{name}", digest=digest,
            labels=ModelLabels(name=name, mode=mode, resources=resources), source="ghcr")
        from app.registry.queries import list_requests
        req = (await list_requests(s, status=states.REQ_PENDING))[0]
        model, _ = await env.registry.approve(s, req.id)
        await s.commit()
        return model.id


async def deploy(env, model_id, digest="sha256:a"):
    async with env.sm() as s:
        from app.registry.queries import get_model
        model = await get_model(s, model_id)
        await env.deployer.begin(s, model, digest)
        await s.commit()
    await env.deployer.run(model_id, digest)
    await env.events.drain()


async def get(env, name="ecg"):
    async with env.sm() as s:
        return await get_model_by_name(s, name)


async def wait_until(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


async def test_first_deploy_succeeds_and_publishes_route_and_schema(env):
    mid = await make_model(env)
    await deploy(env, mid)
    model = await get(env)
    assert model.state == states.READY and model.current_digest == "sha256:a"
    assert model.pending_digest is None
    assert json.loads(await env.redis.get("route:ecg")) == {"url": "http://fake-ecg:8000", "state": "ready", "mode": "sync"}
    assert json.loads(await env.redis.get("schema:ecg"))["model_version"] == "1.0.0"
    async with env.sm() as s:
        deps = await list_deployments(s, mid)
    assert [d.status for d in deps] == ["succeeded"]
    assert env.notifier.names() == ["approval.requested", "approval.approved", "deploy.started", "deploy.succeeded"]


async def test_workload_uses_approved_resources_and_digest_pinned_image(env):
    mid = await make_model(env)
    async with env.sm() as s:       # an image later asks for far more; approval is unchanged
        model = await get_model_by_name(s, "ecg")
        model.requested_resources = Resources(64000, 64 * GI, True, 100 * GI).to_dict()
        await s.commit()
    await deploy(env, mid)
    spec = env.backend.models["ecg"].spec
    assert spec.resources == RES
    assert spec.image == "ghcr.io/org/ecg@sha256:a"


async def test_waits_for_readiness(env):
    env.backend.ready_after = 4
    mid = await make_model(env)
    await deploy(env, mid)
    assert (await get(env)).state == states.READY


async def test_model_not_loaded_yet_keeps_waiting_then_fails_on_timeout(env):
    env.settings.deploy_timeout = 0.3
    env.probe.loaded = False
    mid = await make_model(env)
    await deploy(env, mid)
    model = await get(env)
    assert model.state == states.FAILED and "timed out" in model.state_detail
    assert model.failed_digest == "sha256:a"


async def test_first_deploy_failure_marks_failed_cleans_up_and_marks_route_unavailable(env):
    env.settings.deploy_timeout = 0.3
    env.backend.never_ready = {"ghcr.io/org/ecg@sha256:a"}
    mid = await make_model(env)
    await deploy(env, mid)
    model = await get(env)
    assert model.state == states.FAILED
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "unavailable"
    assert "ecg" not in env.backend.models
    assert any(c[0] == "delete_model" for c in env.backend.calls)
    ev = [e for e in env.notifier.events if e[0] == "deploy.failed"][0]
    assert ev[3]["rolled_back"] is False
    async with env.sm() as s:
        assert (await list_deployments(s, mid))[0].status == "failed"


async def test_failed_upgrade_rolls_back_to_previous_version(env):
    env.settings.deploy_timeout = 0.3
    mid = await make_model(env)
    await deploy(env, mid, "sha256:a")
    env.backend.never_ready = {"ghcr.io/org/ecg@sha256:b"}
    await deploy(env, mid, "sha256:b")
    model = await get(env)
    assert model.state == states.READY and model.current_digest == "sha256:a"
    assert model.failed_digest == "sha256:b" and "failed" in model.state_detail
    assert env.backend.models["ecg"].spec.image == "ghcr.io/org/ecg@sha256:a"
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "ready"
    ev = [e for e in env.notifier.events if e[0] == "deploy.failed"][0]
    assert ev[3]["rolled_back"] is True
    async with env.sm() as s:
        assert (await list_deployments(s, mid))[0].status == "rolled_back"


async def test_successful_upgrade_records_previous_digest(env):
    mid = await make_model(env)
    await deploy(env, mid, "sha256:a")
    await deploy(env, mid, "sha256:b")
    model = await get(env)
    assert (model.current_digest, model.previous_digest) == ("sha256:b", "sha256:a")


async def test_identity_mismatch_fails_the_deploy(env):
    env.probe.info_name = "someone-else"
    mid = await make_model(env)
    await deploy(env, mid)
    model = await get(env)
    assert model.state == states.FAILED and "identity" in model.state_detail
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "unavailable"


async def test_backend_error_on_apply_is_a_failed_deploy(env):
    env.backend.fail_apply = BackendError("registry unreachable")
    mid = await make_model(env)
    await deploy(env, mid)
    model = await get(env)
    assert model.state == states.FAILED and "registry unreachable" in model.state_detail


async def test_rollback_failure_leaves_model_failed_with_both_reasons(env):
    mid = await make_model(env)
    await deploy(env, mid, "sha256:a")
    env.backend.fail_apply = BackendError("cluster down")
    await deploy(env, mid, "sha256:b")
    model = await get(env)
    assert model.state == states.FAILED
    assert "rollback also failed" in model.state_detail
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "unavailable"


async def test_begin_rejects_invalid_transitions(env):
    mid = await make_model(env)
    async with env.sm() as s:
        from app.registry.queries import get_model
        model = await get_model(s, mid)
        await env.deployer.begin(s, model, "sha256:a")
        with pytest.raises(Conflict):
            await env.deployer.begin(s, model, "sha256:a")     # already deploying
        model.state = states.REMOVED
        with pytest.raises(Conflict):
            await env.deployer.begin(s, model, "sha256:a")
        model.state, model.approved_resources = states.PENDING_APPROVAL, None
        with pytest.raises(Conflict):
            await env.deployer.begin(s, model, "sha256:a")     # nothing approved


async def test_concurrent_runs_for_one_model_are_serialised(env):
    env.backend.ready_after = 3
    mid = await make_model(env)
    async with env.sm() as s:
        from app.registry.queries import get_model
        model = await get_model(s, mid)
        await env.deployer.begin(s, model, "sha256:a")
        await s.commit()
    await asyncio.gather(env.deployer.run(mid, "sha256:a"), env.deployer.run(mid, "sha256:b"))
    applies = [c[2] for c in env.backend.calls if c[0] == "apply_model"]
    assert applies == ["ghcr.io/org/ecg@sha256:a", "ghcr.io/org/ecg@sha256:b"]
    assert (await get(env)).current_digest == "sha256:b"


async def test_gpu_model_waits_for_slice_without_failing(env):
    env.settings.deploy_timeout = 0.2
    env.backend.gpu_slots = 0
    mid = await make_model(env, resources=GPU_RES)
    async with env.sm() as s:
        from app.registry.queries import get_model
        await env.deployer.begin(s, await get_model(s, mid), "sha256:a")
        await s.commit()
    task = env.deployer.spawn(mid, "sha256:a")

    async def waiting():
        return (await get(env)).state == states.WAITING_FOR_GPU
    await wait_until(waiting)
    await asyncio.sleep(0.6)                      # three times deploy_timeout: still not failed
    assert (await get(env)).state == states.WAITING_FOR_GPU
    env.backend.gpu_slots = 1
    await asyncio.wait_for(task, 3)
    assert (await get(env)).state == states.READY
    await env.events.drain()
    assert "model.waiting_for_gpu" in env.notifier.names()


async def _ready_model(env, **kw):
    mid = await make_model(env, **kw)
    await deploy(env, mid)
    return mid


async def test_sleep_and_wake_sync_model(env):
    mid = await _ready_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        await env.deployer.sleep(s, model)
        await s.commit()
    assert (await get(env)).state == states.SLEEPING
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "sleeping"
    assert (await env.backend.status("ecg")).replicas == 0

    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        await env.deployer.begin_wake(s, model)
        await s.commit()
    await env.deployer.run_wake(mid)
    await env.events.drain()
    assert (await get(env)).state == states.READY
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "ready"
    assert {"model.sleeping", "model.woke"} <= set(env.notifier.names())


async def test_sleep_wake_guards(env):
    await _ready_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        with pytest.raises(Conflict):
            await env.deployer.begin_wake(s, model)          # not sleeping
        model.state = states.DEPLOYING
        with pytest.raises(Conflict):
            await env.deployer.sleep(s, model)               # not ready


async def test_queue_mode_sleep_only_scales_worker_and_keeps_route_ready(env):
    await _ready_model(env, mode="queue")
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        await env.deployer.sleep(s, model)
        await s.commit()
    assert ("scale", "ecg", 0, "worker") in env.backend.calls
    assert json.loads(await env.redis.get("route:ecg"))["state"] == "ready"
    assert (await get(env)).state == states.SLEEPING


async def test_remove_cleans_everything(env):
    await _ready_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        await env.deployer.remove(s, model, keep_cache=True)
        await s.commit()
    model = await get(env)
    assert model.state == states.REMOVED and model.current_digest is None
    assert await env.redis.exists("route:ecg", "schema:ecg") == 0
    assert "ecg" not in env.backend.models
    assert ("delete_model", "ecg", True) in env.backend.calls
    await env.events.drain()
    assert "model.removed" in env.notifier.names()


async def test_cannot_remove_while_deploying(env):
    mid = await make_model(env)
    async with env.sm() as s:
        from app.registry.queries import get_model
        model = await get_model(s, mid)
        await env.deployer.begin(s, model, "sha256:a")
        with pytest.raises(Conflict):
            await env.deployer.remove(s, model)


async def test_redeploy_and_rollback_selection(env):
    mid = await make_model(env)
    await deploy(env, mid, "sha256:a")
    await deploy(env, mid, "sha256:b")
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        assert await env.deployer.rollback(s, model) == "sha256:a"
        model.state = states.READY
        model.failed_digest = "sha256:x"
        assert await env.deployer.redeploy(s, model) == "sha256:b"
        assert model.failed_digest is None


async def test_rollback_without_previous_and_redeploy_without_anything(env):
    mid = await make_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        with pytest.raises(Conflict):
            await env.deployer.rollback(s, model)
        with pytest.raises(Conflict):
            await env.deployer.redeploy(s, model)


@pytest.mark.parametrize(
    "patch",
    [{"env": {"lower": "x"}}, {"env": {"FASTMLAPI_ROLE": "worker"}}, {"env": {"MLAPI_X": "1"}},
     {"env": {"A": 1}}, {"env": []}, {"secret_refs": ["Bad Name"]}, {"idle_timeout_s": -1},
     {"idle_timeout_s": True}, {"max_job_seconds": 0}, {"nope": 1}, {"env": {"A\n": "x"}}],
)
async def test_config_validation(env, patch):
    await make_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        with pytest.raises(Invalid):
            await env.deployer.update_config(s, model, patch)


async def test_config_env_change_triggers_redeploy_but_idle_change_does_not(env):
    await _ready_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        assert await env.deployer.update_config(s, model, {"idle_timeout_s": 60}) is False
        assert model.state == states.READY
        assert await env.deployer.update_config(s, model, {"env": {"HF_TOKEN": "x"}}) is True
        assert model.state == states.DEPLOYING
        assert model.config == {"idle_timeout_s": 60, "env": {"HF_TOKEN": "x"}}
