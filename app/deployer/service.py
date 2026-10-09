import asyncio
import logging
import re
import time

from app.cluster.backend import BackendError, ClusterBackend, ModelSpec
from app.config import Settings
from app.events import EventBus
from app.queue_access.acl import QueueAccessError
from app.redis_sync.routes import delete_route, delete_schema, publish_route, publish_schema
from app.registry import states
from app.registry.errors import Conflict, Invalid
from app.registry.queries import add_deployment, get_model
from app.registry.resources import Resources
from app.registry.tables import Deployment, Model

logger = logging.getLogger(__name__)

BUSY_STATES = (states.DEPLOYING, states.STARTING, states.WAITING_FOR_GPU)
_ENV_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")
_RESERVED_ENV_PREFIXES = ("FASTMLAPI_", "MLAPI_")
CONFIG_FIELDS = ("env", "secret_refs", "idle_timeout_s", "max_job_seconds")


class DeployFailed(Exception):
    pass


def validate_config(patch: dict) -> dict:
    """Validates a model config patch and returns the cleaned values."""
    clean: dict = {}
    for field, value in patch.items():
        if field == "env":
            if not isinstance(value, dict):
                raise Invalid("env must be an object")
            for key, val in value.items():
                if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
                    raise Invalid(f"invalid env variable name: {key!r}")
                if key.startswith(_RESERVED_ENV_PREFIXES):
                    raise Invalid(f"env variable {key!r} is reserved by the platform")
                if not isinstance(val, str) or len(val) > 4096:
                    raise Invalid(f"env value for {key!r} must be a string up to 4096 characters")
            clean[field] = dict(value)
        elif field == "secret_refs":
            if not isinstance(value, list) or not all(
                isinstance(v, str) and _NAME_RE.fullmatch(v) for v in value
            ):
                raise Invalid("secret_refs must be a list of DNS-style names")
            clean[field] = list(value)
        elif field == "idle_timeout_s":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 604800:
                raise Invalid("idle_timeout_s must be an integer between 0 and 604800")
            clean[field] = value
        elif field == "max_job_seconds":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 86400:
                raise Invalid("max_job_seconds must be an integer between 1 and 86400")
            clean[field] = value
        else:
            raise Invalid(f"unknown config field: {field!r}")
    return clean


class Deployer:
    """Deploys approved model versions, rolls back failures, and handles sleep/wake/remove."""

    def __init__(
        self,
        *,
        sessionmaker,
        backend: ClusterBackend,
        redis,
        events: EventBus | None,
        probe,
        settings: Settings,
        queue_access=None,
    ):
        self._sm = sessionmaker
        self._backend = backend
        self._redis = redis
        self._events = events
        self._probe = probe
        self._settings = settings
        self._queue_access = queue_access
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    # ───────────── plumbing ─────────────
    def _lock(self, model_id: int) -> asyncio.Lock:
        return self._locks.setdefault(model_id, asyncio.Lock())

    def _emit(self, event: str, name: str, digest: str | None, status: str | None, **details) -> None:
        if self._events is not None:
            self._events.emit(event, name, digest=digest, status=status, **details)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0)

    async def _update(self, model_id: int, **fields) -> None:
        async with self._sm() as session:
            model = await get_model(session, model_id)
            for name, value in fields.items():
                setattr(model, name, value)
            await session.commit()

    async def _set_route(self, name: str, state: str, mode: str) -> None:
        await publish_route(self._redis, name, url=await self._backend.endpoint(name), state=state, mode=mode)

    async def _spec(self, model: Model, digest: str) -> ModelSpec:
        approved = Resources.from_dict(model.approved_resources)
        image = f"{model.image}@{digest}" if model.source == "ghcr" else digest
        config = model.config or {}
        queue_url = None
        if model.mode == "queue":
            if self._queue_access is None:
                raise DeployFailed("queue-mode models need QUEUE_ACL_SECRET to be configured")
            try:
                queue_url = await self._queue_access.provision(model.name)
            except QueueAccessError as exc:
                raise DeployFailed(str(exc)) from exc
        return ModelSpec(
            name=model.name,
            image=image,
            mode=model.mode,
            resources=approved,  # always the approved values, never the image labels
            env=dict(config.get("env", {})),
            secret_refs=tuple(config.get("secret_refs", ())),
            queue_url=queue_url,
            max_job_seconds=int(config.get("max_job_seconds", 600)),
            api_resources=Resources(
                cpu_m=self._settings.api_cpu_m,
                memory_bytes=self._settings.api_memory_bytes,
                gpu=False,
                disk_bytes=0,
            ),
        )

    # ───────────── deploy ─────────────
    async def begin(self, session, model: Model, digest: str) -> None:
        """Marks the model as deploying. Raises Conflict when the transition is not allowed."""
        if model.state in BUSY_STATES:
            raise Conflict(f"model {model.name!r} is busy ({model.state})")
        if model.state == states.REMOVED:
            raise Conflict(f"model {model.name!r} was removed")
        if not model.approved_resources:
            raise Conflict(f"resources for model {model.name!r} are not approved yet")
        model.state, model.state_detail = states.DEPLOYING, None
        await session.flush()

    def spawn(self, model_id: int, digest: str) -> asyncio.Task:
        return self._spawn(self.run(model_id, digest))

    async def run(self, model_id: int, digest: str) -> None:
        async with self._lock(model_id):
            async with self._sm() as session:
                model = await get_model(session, model_id)
                if model is None or model.state == states.REMOVED:
                    return
                name, mode, old_current = model.name, model.mode, model.current_digest
                deployment = await add_deployment(session, model.id, digest)
                model.state = states.DEPLOYING
                await session.commit()
                deployment_id = deployment.id
            self._emit("deploy.started", name, digest, states.DEPLOYING)

            try:
                await self._deploy_version(model_id, name, mode, digest, first=old_current is None)
            except (DeployFailed, BackendError) as exc:
                await self._handle_failure(model_id, name, mode, digest, old_current, deployment_id, str(exc))
                return
            except Exception as exc:  # unexpected bug: still never leave the model "deploying"
                logger.exception("Unexpected error while deploying %s", name)
                await self._handle_failure(model_id, name, mode, digest, old_current, deployment_id, f"internal error: {exc}")
                return

            url = await self._backend.endpoint(name)
            schema = await self._probe.schema(url)
            new_state = states.READY
            if mode == "queue" and (await self._backend.status(name)).worker_replicas == 0:
                new_state = states.SLEEPING  # the API is up; the worker starts when jobs arrive
            async with self._sm() as session:
                model = await get_model(session, model_id)
                if old_current and old_current != digest:
                    model.previous_digest = old_current
                model.current_digest = digest
                if model.pending_digest == digest:
                    model.pending_digest = None
                model.state, model.state_detail = new_state, None
                dep = await session.get(Deployment, deployment_id)
                dep.status, dep.finished_at = states.DEP_SUCCEEDED, int(time.time())
                await session.commit()
            await self._set_route(name, states.READY, mode)
            if schema:
                await publish_schema(self._redis, name, schema)
            self._emit("deploy.succeeded", name, digest, new_state)

    async def _deploy_version(self, model_id: int, name: str, mode: str, digest: str, *, first: bool) -> None:
        async with self._sm() as session:
            model = await get_model(session, model_id)
            spec = await self._spec(model, digest)
        await self._backend.apply_model(spec)
        if first:
            await self._set_route(name, states.STARTING, mode)
        await self._wait_ready(model_id, name, mode)
        url = await self._backend.endpoint(name)
        info = await self._probe.info(url)
        if info is None or info.get("name") != name:
            raise DeployFailed(f"model identity check failed: expected {name!r}, got {None if info is None else info.get('name')!r}")

    async def _wait_ready(self, model_id: int, name: str, mode: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.deploy_timeout
        gpu_deadline = loop.time() + self._settings.gpu_wait_max
        reported_gpu_wait = False
        url = await self._backend.endpoint(name)
        while True:
            status = await self._backend.status(name)
            if not status.exists:
                raise DeployFailed("workload disappeared while waiting for it to become ready")
            if status.pending_reason and "gpu" in status.pending_reason.lower():
                deadline = loop.time() + self._settings.deploy_timeout  # waiting for a GPU is not a failure
                if loop.time() > gpu_deadline:
                    raise DeployFailed("gave up waiting for a free GPU slice")
                if not reported_gpu_wait:
                    reported_gpu_wait = True
                    await self._update(model_id, state=states.WAITING_FOR_GPU, state_detail=status.pending_reason)
                    self._emit("model.waiting_for_gpu", name, None, states.WAITING_FOR_GPU)
            elif status.pending_reason and loop.time() > deadline:
                raise DeployFailed(status.pending_reason)
            elif status.ready:
                if reported_gpu_wait:
                    await self._update(model_id, state=states.DEPLOYING, state_detail=None)
                    reported_gpu_wait = False
                health = await self._probe.health(url)
                if health.ok and health.model_loaded is not False:
                    return
            if status.restarts > self._settings.max_restarts:
                raise DeployFailed(f"container keeps crashing ({status.restarts} restarts)")
            if loop.time() > deadline:
                raise DeployFailed("timed out waiting for the model to become ready")
            await asyncio.sleep(self._settings.deploy_poll_interval)

    async def _worker_loaded(self, name: str) -> bool:
        """True when a live worker reports that its model is loaded (fastmlapi heartbeat keys)."""
        async for key in self._redis.scan_iter(match=f"fastmlapi:{name}:workers:*"):
            loaded = await self._redis.hget(key, "model_loaded")
            if loaded is not None and str(loaded).lower() in ("true", "1"):
                return True
        return False

    async def _wait_worker_ready(self, name: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.deploy_timeout
        while not await self._worker_loaded(name):
            status = await self._backend.status(name)
            if status.pending_reason and "gpu" in status.pending_reason.lower():
                deadline = loop.time() + self._settings.deploy_timeout  # waiting for a GPU is not a failure
            if loop.time() > deadline:
                raise DeployFailed("worker did not report a loaded model in time")
            await asyncio.sleep(self._settings.deploy_poll_interval)

    async def _handle_failure(
        self, model_id: int, name: str, mode: str, digest: str, old_current: str | None, deployment_id: int, reason: str
    ) -> None:
        logger.warning("Deploy of %s %s failed: %s", name, digest, reason)
        rolled_back = False
        if old_current:
            try:
                await self._deploy_version(model_id, name, mode, old_current, first=False)
                rolled_back = True
            except Exception as exc:
                reason = f"{reason}; rollback also failed: {exc}"
        async with self._sm() as session:
            model = await get_model(session, model_id)
            model.failed_digest = digest
            if model.pending_digest == digest:
                model.pending_digest = None
            if rolled_back:
                model.state, model.state_detail = states.READY, f"deploy of {digest} failed: {reason}"
            else:
                model.state, model.state_detail = states.FAILED, reason
            dep = await session.get(Deployment, deployment_id)
            dep.status = states.DEP_ROLLED_BACK if rolled_back else states.DEP_FAILED
            dep.reason, dep.finished_at = reason, int(time.time())
            await session.commit()
            final_state = model.state
        if rolled_back:
            await self._set_route(name, states.READY, mode)
        else:
            await self._set_route(name, "unavailable", mode)
            try:
                await self._backend.delete_model(name, keep_cache=True)
            except Exception:
                logger.exception("Could not clean up failed deployment of %s", name)
        self._emit("deploy.failed", name, digest, final_state, reason=reason, rolled_back=rolled_back)

    async def recover_interrupted(self) -> None:
        """After a restart, no deploy task is running: repair models stuck in a busy state."""
        from sqlalchemy import select

        async with self._sm() as session:
            busy = (await session.scalars(select(Model).where(Model.state.in_(BUSY_STATES)))).all()
            for model in busy:
                logger.warning("Model %s was %s when the supervisor stopped", model.name, model.state)
                if model.current_digest:
                    model.state, model.state_detail = states.READY, "a deploy was interrupted by a restart"
                else:
                    model.state, model.state_detail = states.FAILED, "deploy interrupted by a supervisor restart"
                running = (
                    await session.scalars(
                        select(Deployment).where(Deployment.model_id == model.id, Deployment.status == "started")
                    )
                ).all()
                for dep in running:
                    dep.status, dep.reason, dep.finished_at = states.DEP_FAILED, "interrupted by restart", int(time.time())
            await session.commit()

    # ───────────── admin operations ─────────────
    async def redeploy(self, session, model: Model) -> str:
        digest = model.current_digest or model.failed_digest
        if digest is None:
            raise Conflict(f"model {model.name!r} has nothing to redeploy")
        await self.begin(session, model, digest)
        model.failed_digest = None
        return digest

    async def rollback(self, session, model: Model) -> str:
        if not model.previous_digest:
            raise Conflict(f"model {model.name!r} has no previous version")
        digest = model.previous_digest
        await self.begin(session, model, digest)
        return digest

    async def sleep(self, session, model: Model) -> None:
        if model.state != states.READY:
            raise Conflict(f"model {model.name!r} is {model.state}; only ready models can sleep")
        role = "worker" if model.mode == "queue" else "main"
        await self._backend.scale(model.name, 0, role=role)
        model.state = states.SLEEPING
        await session.flush()
        if model.mode == "sync":
            await self._set_route(model.name, states.SLEEPING, model.mode)
        self._emit("model.sleeping", model.name, model.current_digest, states.SLEEPING)

    async def begin_wake(self, session, model: Model) -> None:
        if model.state != states.SLEEPING:
            raise Conflict(f"model {model.name!r} is {model.state}; only sleeping models can wake")
        model.state = states.STARTING
        await session.flush()

    def spawn_wake(self, model_id: int) -> asyncio.Task:
        return self._spawn(self.run_wake(model_id))

    async def run_wake(self, model_id: int) -> None:
        async with self._lock(model_id):
            async with self._sm() as session:
                model = await get_model(session, model_id)
                if model is None or model.state != states.STARTING:
                    return
                name, mode, digest = model.name, model.mode, model.current_digest
            try:
                if mode == "sync":
                    await self._set_route(name, states.STARTING, mode)
                    await self._backend.scale(name, 1, role="main")
                    await self._wait_ready(model_id, name, mode)
                else:
                    await self._backend.scale(name, 1, role="worker")
                    await self._wait_worker_ready(name)
            except (DeployFailed, BackendError) as exc:
                await self._update(model_id, state=states.FAILED, state_detail=f"wake failed: {exc}")
                await self._set_route(name, "unavailable", mode)
                self._emit("deploy.failed", name, digest, states.FAILED, reason=f"wake failed: {exc}", rolled_back=False)
                return
            await self._update(model_id, state=states.READY, state_detail=None)
            await self._set_route(name, states.READY, mode)
            self._emit("model.woke", name, digest, states.READY)

    async def remove(self, session, model: Model, *, keep_cache: bool = False) -> None:
        if model.state in BUSY_STATES:
            raise Conflict(f"model {model.name!r} is busy ({model.state})")
        await self._backend.delete_model(model.name, keep_cache=keep_cache)
        await delete_route(self._redis, model.name)
        await delete_schema(self._redis, model.name)
        if self._queue_access is not None and model.mode == "queue":
            await self._queue_access.remove(model.name)
        digest = model.current_digest
        model.state, model.state_detail = states.REMOVED, None
        model.current_digest = model.previous_digest = model.pending_digest = None
        await session.flush()
        self._emit("model.removed", model.name, digest, states.REMOVED)

    async def update_config(self, session, model: Model, patch: dict) -> bool:
        """Applies a config patch. Returns True when a redeploy was started."""
        clean = validate_config(patch)
        needs_redeploy = any(
            clean[k] != (model.config or {}).get(k) for k in ("env", "secret_refs") if k in clean
        )
        model.config = {**(model.config or {}), **clean}
        if not needs_redeploy:
            await session.flush()
            return False
        if model.state == states.READY:
            await self.redeploy(session, model)
            return True
        model.state_detail = "config changes apply on the next deploy"
        await session.flush()
        return False
