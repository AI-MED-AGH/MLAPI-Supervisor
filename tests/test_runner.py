import asyncio

from app.config import Settings
from app.lifecycle.runner import Background
from app.registry import states
from app.registry.queries import get_model_by_name, list_deployments
from tests.support import _ready_model, get, make_model, deploy


class Ticker:
    def __init__(self, fail_first=False):
        self.calls = 0
        self.fail_first = fail_first

    async def tick(self):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("boom")
        return []


class FakeWake:
    def __init__(self):
        self.calls = 0

    async def process_one(self, timeout=1):
        self.calls += 1
        await asyncio.sleep(0.01)


def settings():
    return Settings(_env_file=None, reaper_interval=0.01, queue_poll_interval=0.01,
                    reconcile_interval=0.01, poll_interval=0.01)


async def test_all_loops_run_and_stop_cleanly():
    reaper, scaler, recon, wake = Ticker(), Ticker(), Ticker(), FakeWake()
    bg = Background(reaper=reaper, scaler=scaler, reconciler=recon, wake=wake, poller=None, settings=settings())
    bg.start()
    await asyncio.sleep(0.15)
    await bg.stop()
    assert min(reaper.calls, scaler.calls, recon.calls, wake.calls) >= 2
    frozen = (reaper.calls, wake.calls)
    await asyncio.sleep(0.05)
    assert (reaper.calls, wake.calls) == frozen          # nothing runs after stop


async def test_a_failing_tick_does_not_kill_the_loop():
    reaper = Ticker(fail_first=True)
    bg = Background(reaper=reaper, scaler=Ticker(), reconciler=Ticker(), wake=FakeWake(), poller=None, settings=settings())
    bg.start()
    await asyncio.sleep(0.1)
    await bg.stop()
    assert reaper.calls >= 3


async def test_poller_is_optional_and_runs_when_given():
    poller = Ticker()
    bg = Background(reaper=Ticker(), scaler=Ticker(), reconciler=Ticker(), wake=FakeWake(), poller=poller, settings=settings())
    bg.start()
    await asyncio.sleep(0.08)
    await bg.stop()
    assert poller.calls >= 2


async def test_stop_without_start_is_safe():
    bg = Background(reaper=Ticker(), scaler=Ticker(), reconciler=Ticker(), wake=FakeWake(), poller=None, settings=settings())
    await bg.stop()


# ───────────── recovery of deploys interrupted by a restart ─────────────
async def test_interrupted_first_deploy_is_marked_failed(env):
    mid = await make_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        await env.deployer.begin(s, model, "sha256:a")
        from app.registry.queries import add_deployment
        await add_deployment(s, model.id, "sha256:a")
        await s.commit()
    await env.deployer.recover_interrupted()
    model = await get(env)
    assert model.state == states.FAILED and "interrupted" in model.state_detail
    async with env.sm() as s:
        assert (await list_deployments(s, mid))[0].status == "failed"


async def test_interrupted_upgrade_re_verifies_the_running_version(env):
    """The cluster may be running a half-applied new version; recovery must deploy the known-good one again."""
    mid = await _ready_model(env)
    async with env.sm() as s:
        model = await get_model_by_name(s, "ecg")
        model.state = states.DEPLOYING
        await s.commit()
    applies_before = len([c for c in env.backend.calls if c[0] == "apply_model"])
    await env.deployer.recover_interrupted()
    await env.deployer.drain()
    model = await get(env)
    assert model.state == states.READY and model.current_digest == "sha256:a"
    assert len([c for c in env.backend.calls if c[0] == "apply_model"]) == applies_before + 1


async def test_recovery_leaves_healthy_models_alone(env):
    await _ready_model(env)
    await env.deployer.recover_interrupted()
    assert (await get(env)).state == states.READY
