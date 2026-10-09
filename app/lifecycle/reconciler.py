import logging

from app.redis_sync.keys import rebuild_keys
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

    async def _active_names(self) -> set[str]:
        """Names of models that must keep their workload and route. Always read fresh: models appear mid-pass."""
        async with self._sm() as session:
            return {m.name for m in await list_models(session) if m.state != states.REMOVED}

    async def tick(self) -> None:
        try:  # Redis may have restarted since the last pass, taking every API key with it
            async with self._sm() as session:
                await rebuild_keys(self._redis, session)
        except Exception:
            logger.exception("Could not republish API keys")

        async with self._sm() as session:
            models = list(await list_models(session))
        for model in models:
            try:
                await self._reconcile_model(model)
            except Exception:
                logger.exception("Reconciling %s failed", model.name)

        # Orphan sweeps come last, and the set of live models is re-read right before each one: a model approved and
        # deployed since this pass began has a workload and a route that must not be mistaken for leftovers.
        workloads = await self._backend.list_models()
        active = await self._active_names()
        for name in workloads:
            if name not in active:
                logger.warning("Removing orphaned workload %s", name)
                await self._backend.delete_model(name, keep_cache=True)
        for prefix in ("route:", "schema:"):
            keys = [key async for key in self._redis.scan_iter(match=f"{prefix}*")]
            active = await self._active_names()
            for key in keys:
                if key.removeprefix(prefix) not in active:
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
