from dataclasses import dataclass

from fastapi import FastAPI, Request

from app.deployer.probe import HttpProbe
from app.deployer.service import Deployer
from app.events import EventBus
from app.registry.service import RegistryService


@dataclass
class Services:
    backend: object
    probe: object
    inspector: object | None
    events: EventBus
    registry: RegistryService
    deployer: Deployer
    queue_access: object | None = None
    queue_redis: object | None = None
    _owned: tuple = ()

    async def aclose(self) -> None:
        for resource in self._owned:
            await resource.aclose()


def build_inspector(state, backend):
    """Local images are read from the Docker engine; GHCR images need GHCR_ORG and GHCR_TOKEN."""
    import httpx

    from app.deployer.inspectors import CompositeInspector, GhcrInspector
    from app.poller.ghcr import GhcrClient

    settings = state.settings
    local = None
    if hasattr(backend, "client"):  # the Docker backend
        from app.cluster.docker import DockerInspector

        local = DockerInspector(backend.client)
    ghcr = None
    if settings.ghcr_org and settings.ghcr_token:
        http = httpx.AsyncClient(timeout=20, follow_redirects=True)
        ghcr = GhcrInspector(GhcrClient(token=settings.ghcr_token, org=settings.ghcr_org, http=http), settings.ghcr_org)
    if local is None and ghcr is None:
        return None
    return CompositeInspector(local=local, ghcr=ghcr)


def build_queue_redis(state):
    """Returns (client, owned): the dedicated queue Redis when configured, else the shared one."""
    if state.settings.queue_redis_url:
        import redis.asyncio as aioredis

        client = aioredis.from_url(state.settings.queue_redis_url, decode_responses=True)
        return client, (client,)
    return state.redis, ()


def build_queue_access(state, queue_redis=None):
    """Per-model Redis ACL users need a master secret; without one queue-mode models cannot deploy."""
    from urllib.parse import urlsplit, urlunsplit

    from app.queue_access.acl import QueueAccess

    settings = state.settings
    if not settings.queue_acl_secret:
        return None
    admin = queue_redis or state.redis
    base = settings.model_redis_url
    if not base:
        parts = urlsplit(settings.redis_url)
        base = urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))
    return QueueAccess(
        admin, secret=settings.queue_acl_secret, model_redis_url=base, dedicated=bool(settings.queue_redis_url)
    )


def build_services(app: FastAPI) -> Services:
    from app.cluster.factory import make_backend

    state = app.state
    backend = state.backend or make_backend(state.settings)
    state.backend = backend
    probe = state.probe or HttpProbe()
    events = EventBus(state.sessionmaker)
    queue_redis, owned = build_queue_redis(state)
    queue_access = getattr(state, "queue_access", None) or build_queue_access(state, queue_redis)
    deployer = Deployer(
        sessionmaker=state.sessionmaker,
        backend=backend,
        redis=state.redis,
        events=events,
        probe=probe,
        settings=state.settings,
        queue_access=queue_access,
        queue_redis=queue_redis,
    )
    return Services(
        backend=backend,
        probe=probe,
        inspector=state.inspector or build_inspector(state, backend),
        events=events,
        registry=RegistryService(events),
        deployer=deployer,
        queue_access=queue_access,
        queue_redis=queue_redis,
        _owned=owned,
    )


def get_services(request: Request) -> Services:
    state = request.app.state
    if state.services is None:
        state.services = build_services(request.app)
    return state.services
