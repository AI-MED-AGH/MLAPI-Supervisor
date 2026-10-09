import logging

from app.redis_sync.routes import delete_route, delete_schema, publish_route, publish_schema
from app.registry import states
from app.registry.errors import Conflict
from app.registry.queries import list_models

logger = logging.getLogger(__name__)


def desired_route_state(state: str, mode: str) -> str | None:
    if state == states.READY:
        return "ready"
    if state == states.SLEEPING:
        return "sleeping" if mode == "sync" else "ready"  # a sleeping queue model still has its API up
    if state == states.FAILED:
        return "unavailable"
    return None


class Reconciler:
    """Makes the cluster and Redis match the database: heals drift, restores routes, removes orphans."""

    def __init__(self, *, sessionmaker, deployer, redis, backend, probe, queue_access=None):
        self._sm = sessionmaker
        self._deployer = deployer
        self._redis = redis
        self._backend = backend
        self._probe = probe
        self._queue_access = queue_access

    async def tick(self) -> None:
        async with self._sm() as session:
            models = list(await list_models(session))
        active = {m.name for m in models if m.state != states.REMOVED}

        for model in models:
            try:
                await self._reconcile_model(model)
            except Exception:
                logger.exception("Reconciling %s failed", model.name)

        for name in await self._backend.list_models():
            if name not in active:
                logger.warning("Removing orphaned workload %s", name)
                await self._backend.delete_model(name, keep_cache=True)
        async for key in self._redis.scan_iter(match="route:*"):
            if key.removeprefix("route:") not in active:
                await self._redis.delete(key)
        async for key in self._redis.scan_iter(match="schema:*"):
            if key.removeprefix("schema:") not in active:
                await self._redis.delete(key)

    async def _reconcile_model(self, model) -> None:
        if model.state == states.REMOVED:
            await delete_route(self._redis, model.name)
            await delete_schema(self._redis, model.name)
            return
        if model.state in (states.DEPLOYING, states.STARTING, states.WAITING_FOR_GPU):
            return
        if model.state in (states.READY, states.SLEEPING) and model.current_digest:
            runtime = await self._backend.status(model.name)
            if not runtime.exists:
                await self._heal_missing_workload(model)
                return
        if model.mode == "queue" and self._queue_access is not None and model.current_digest:
            try:  # idempotent: restores the ACL user after a Redis restart
                await self._queue_access.provision(model.name)
            except Exception:
                logger.warning("Could not refresh the queue ACL user for %s", model.name)
        wanted = desired_route_state(model.state, model.mode)
        if wanted is None:
            return
        url = await self._backend.endpoint(model.name)
        await publish_route(self._redis, model.name, url=url, state=wanted, mode=model.mode)
        if model.state == states.READY and not await self._redis.exists(f"schema:{model.name}"):
            schema = await self._probe.schema(url)
            if schema:
                await publish_schema(self._redis, model.name, schema)

    async def _heal_missing_workload(self, model) -> None:
        logger.warning("Workload for %s is missing; redeploying %s", model.name, model.current_digest)
        async with self._sm() as session:
            from app.registry.queries import get_model

            fresh = await get_model(session, model.id)
            try:
                await self._deployer.begin(session, fresh, fresh.current_digest)
                await session.commit()
            except Conflict:
                await session.rollback()
                return
        self._deployer.spawn(model.id, model.current_digest)
