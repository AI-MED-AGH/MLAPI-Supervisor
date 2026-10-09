import asyncio

import pytest

from app.cluster.fake import FakeBackend
from app.config import Settings
from app.deployer.service import Deployer
from app.events import EventBus
from app.registry import states
from app.registry.labels import ModelLabels
from app.registry.queries import get_model, get_model_by_name, list_requests
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


class FakeQueueAccess:
    """Stands in for the Redis ACL manager in tests that are not about ACLs."""

    def __init__(self):
        self.provisioned, self.removed = [], []

    async def provision(self, name):
        self.provisioned.append(name)
        return f"redis://m_{name}:pw@fake-redis:6379/0"

    async def remove(self, name):
        self.removed.append(name)


def build_env(sessionmaker, redis):
    e = Env()
    e.settings = Settings(_env_file=None, cluster_backend="fake", deploy_timeout=1.5,
                          deploy_poll_interval=0.01, gpu_wait_max=5, auto_migrate=False)
    e.backend, e.probe, e.redis, e.sm = FakeBackend(), FakeProbe(), redis, sessionmaker
    e.notifier = Notifier()
    e.events = EventBus(sessionmaker, e.notifier)
    e.registry = RegistryService(e.events)
    e.deployer = Deployer(sessionmaker=sessionmaker, backend=e.backend, redis=redis,
                          events=e.events, probe=e.probe, settings=e.settings,
                          queue_access=FakeQueueAccess())
    return e


async def make_model(env, *, mode="sync", resources=RES, digest="sha256:a", name="ecg"):
    """Registers and approves a model; returns its id (not yet deployed)."""
    async with env.sm() as s:
        sub = await env.registry.submit_digest(
            s, name=name, image=f"ghcr.io/org/{name}", digest=digest,
            labels=ModelLabels(name=name, mode=mode, resources=resources), source="ghcr")
        req = (await list_requests(s, status=states.REQ_PENDING))[0]
        model, _ = await env.registry.approve(s, req.id)
        await s.commit()
        return model.id


async def deploy(env, model_id, digest="sha256:a"):
    async with env.sm() as s:
        model = await get_model(s, model_id)
        await env.deployer.begin(s, model, digest)
        await s.commit()
    await env.deployer.run(model_id, digest)
    await env.events.drain()


async def get(env, name="ecg"):
    async with env.sm() as s:
        return await get_model_by_name(s, name)


async def wait_until(predicate, timeout=8.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")




async def _ready_model(env, **kw):
    mid = await make_model(env, **kw)
    await deploy(env, mid)
    return mid
