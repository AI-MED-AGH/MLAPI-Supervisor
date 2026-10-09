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
_RESERVED_ENV_PREFIXES = ("FASTMLAPI_", "MLAPI_", "NVIDIA_", "CUDA_")
# secrets a model must never be able to mount: the platform's own (image pull, Redis, environment) and cluster internals
_RESERVED_SECRET_PREFIXES = ("ghcr-", "mlapi-", "kube", "default-token")
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
            if any(v.startswith(_RESERVED_SECRET_PREFIXES) for v in value):
                raise Invalid("secret_refs must not name platform secrets (ghcr-*, mlapi-*, kube*)")
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
        queue_redis=None,
    ):
        self._sm = sessionmaker
        self._backend = backend
        self._redis = redis
        self._queue_redis = queue_redis or redis  # where fastmlapi's queue keys live
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

    async def _guarded(self, model_id: int, coro) -> None:
        """Runs a background deploy/wake. If it crashes before its own error handling, the model must not stay busy."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Background task for model %s crashed", model_id)
            try:
                async with self._sm() as session:
                    model = await session.get(Model, model_id)
                    if model is not None and model.state in BUSY_STATES:
                        if model.current_digest:
                            model.state = states.READY if model.mode == "sync" else states.SLEEPING
                            model.state_detail = f"internal error: {exc}"
                        else:
                            model.state, model.state_detail = states.FAILED, f"internal error: {exc}"
                        await session.commit()
            except Exception:
                logger.exception("Could not repair model %s after a crash", model_id)

    def _spawn(self, coro, model_id: int | None = None) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(self._guarded(model_id, coro) if model_id is not None else coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def shutdown(self) -> None:
        """Stops running deploys. A model left mid-deploy is repaired by recover_interrupted at the next start."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._tasks.clear()

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

    @staticmethod
    async def _transition(session, model: Model, allowed_from: tuple[str, ...], to_state: str, detail=None) -> bool:
        """Moves the model to `to_state` only if the DATABASE still says it is in one of `allowed_from`.

        The in-memory copy a request holds may be stale: two requests can both read "ready". A conditional UPDATE decides
        the winner atomically, and the loser gets False."""
        from sqlalchemy import update

        result = await session.execute(
            update(Model).where(Model.id == model.id, Model.state.in_(allowed_from))
            .values(state=to_state, state_detail=detail)
        )
        if result.rowcount != 1:
            await session.refresh(model)
            return False
        model.state, model.state_detail = to_state, detail
        return True

    # ───────────── deploy ─────────────
    async def begin(self, session, model: Model, digest: str) -> None:
        """Marks the model as deploying. Raises Conflict when the transition is not allowed."""
        if model.state in BUSY_STATES:
            raise Conflict(f"model {model.name!r} is busy ({model.state})")
        if model.state == states.REMOVED:
            raise Conflict(f"model {model.name!r} was removed")
        if not model.approved_resources:
            raise Conflict(f"resources for model {model.name!r} are not approved yet")
        startable = tuple(st for st in states.ALL_STATES if st not in BUSY_STATES and st != states.REMOVED)
        if not await self._transition(session, model, startable, states.DEPLOYING):
            raise Conflict(f"model {model.name!r} is busy ({model.state})")

    def spawn(self, model_id: int, digest: str) -> asyncio.Task:
        return self._spawn(self.run(model_id, digest), model_id)

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
                url = await self._backend.endpoint(name)
                schema = await self._best_effort_schema(url)
                new_state = states.READY
                if mode == "queue" and (await self._backend.status(name)).worker_replicas == 0:
                    new_state = states.SLEEPING  # the API is up; the worker starts when jobs arrive
                # publish first, record last: if anything below fails the failure handling below repairs the route
                await self._set_route(name, states.READY, mode)
                if schema:
                    await publish_schema(self._redis, name, schema)
                async with self._sm() as session:
                    model = await get_model(session, model_id)
                    if model.state == states.REMOVED:
                        return
                    if old_current and old_current != digest:
                        model.previous_digest = old_current
                    model.current_digest = digest
                    if model.pending_digest == digest:
                        model.pending_digest = None
                    model.state, model.state_detail = new_state, None
                    dep = await session.get(Deployment, deployment_id)
                    dep.status, dep.finished_at = states.DEP_SUCCEEDED, int(time.time())
                    await session.commit()
            except (DeployFailed, BackendError) as exc:
                await self._handle_failure(model_id, name, mode, digest, old_current, deployment_id, str(exc))
                return
            except Exception as exc:  # a bug or an outage anywhere: still never leave the model "deploying"
                logger.exception("Unexpected error while deploying %s", name)
                await self._handle_failure(model_id, name, mode, digest, old_current, deployment_id, f"internal error: {exc}")
                return
            self._emit("deploy.succeeded", name, digest, new_state)

    async def _best_effort_schema(self, url: str) -> dict | None:
        try:
            return await self._probe.schema(url)
        except Exception:
            logger.warning("Could not read the schema of a model that just deployed", exc_info=True)
            return None

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
                if loop.time() > gpu_deadline:
                    raise DeployFailed("gave up waiting for a free GPU slice")
                if not reported_gpu_wait:
                    reported_gpu_wait = True
                    await self._update(model_id, state=states.WAITING_FOR_GPU, state_detail=status.pending_reason)
                    self._emit("model.waiting_for_gpu", name, None, states.WAITING_FOR_GPU)
                # waiting for a GPU is not a failure: restart the clock AFTER the awaits above, then poll again
                deadline = loop.time() + self._settings.deploy_timeout
                await asyncio.sleep(self._settings.deploy_poll_interval)
                continue
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

    async def _worker_heartbeats(self, name: str) -> list[bool]:
        """`model_loaded` of every worker heartbeat key fastmlapi has written for this model."""
        loaded: list[bool] = []
        async for key in self._queue_redis.scan_iter(match=f"fastmlapi:{name}:workers:*"):
            value = await self._queue_redis.hget(key, "model_loaded")
            loaded.append(value is not None and str(value).lower() in ("true", "1"))
        return loaded

    async def _wait_worker_ready(self, name: str) -> None:
        """Ready = a worker reports a loaded model. Workers that never write a heartbeat (older fastmlapi) are trusted once
        their container has been up for `worker_heartbeat_grace`; a worker that does report `not loaded` is never trusted."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self._settings.deploy_timeout
        gpu_deadline = started + self._settings.gpu_wait_max
        while True:
            heartbeats = await self._worker_heartbeats(name)
            if any(heartbeats):
                return
            status = await self._backend.status(name)
            if status.pending_reason and "gpu" in status.pending_reason.lower():
                if loop.time() > gpu_deadline:
                    raise DeployFailed("gave up waiting for a free GPU slice")
                started = loop.time()  # the worker is not running yet: the heartbeat grace period has not started
                deadline = loop.time() + self._settings.deploy_timeout
            elif not heartbeats and status.worker_replicas > 0 and loop.time() - started >= self._settings.worker_heartbeat_grace:
                logger.info("Worker of %s has no heartbeat; trusting its running container", name)
                return
            if loop.time() > deadline:
                raise DeployFailed("worker did not report a loaded model in time")
            await asyncio.sleep(self._settings.deploy_poll_interval)

    async def _handle_failure(
        self, model_id: int, name: str, mode: str, digest: str, old_current: str | None, deployment_id: int, reason: str
    ) -> None:
        logger.warning("Deploy of %s %s failed: %s", name, digest, reason)
        async with self._sm() as session:
            current = await get_model(session, model_id)
            if current is None or current.state == states.REMOVED:
                # an admin removed the model while it was deploying: do not bring it back, and tidy what it published
                await delete_route(self._redis, name)
                await delete_schema(self._redis, name)
                try:
                    await self._backend.delete_model(name, keep_cache=True)
                except Exception:
                    logger.warning("Could not clean up the workload of removed model %s", name)
                return
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
        """After a restart no deploy task is running. A first deploy is marked failed; a model that was already serving
        is deployed again from its recorded version, because the cluster may hold a half-applied newer one."""
        from sqlalchemy import select

        redeploy: list[tuple[int, str]] = []
        async with self._sm() as session:
            busy = (await session.scalars(select(Model).where(Model.state.in_(BUSY_STATES)))).all()
            for model in busy:
                logger.warning("Model %s was %s when the supervisor stopped", model.name, model.state)
                if model.current_digest:
                    model.state, model.state_detail = states.READY, "a deploy was interrupted by a restart"
                    redeploy.append((model.id, model.current_digest))
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
        for model_id, digest in redeploy:
            async with self._sm() as session:
                model = await get_model(session, model_id)
                try:
                    await self.begin(session, model, digest)
                except Conflict:
                    continue
                await session.commit()
            self.spawn(model_id, digest)

    # ───────────── admin operations ─────────────
    async def redeploy(self, session, model: Model, *, clear_failed: bool = True) -> str:
        digest = model.current_digest or model.failed_digest
        if digest is None:
            raise Conflict(f"model {model.name!r} has nothing to redeploy")
        await self.begin(session, model, digest)
        if clear_failed:  # an explicit "try again" forgets a known-bad digest; a config change must not
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
        # claim the transition first so a concurrent redeploy/sleep cannot also act on this model
        if not await self._transition(session, model, (states.READY,), states.SLEEPING):
            raise Conflict(f"model {model.name!r} is {model.state}; only ready models can sleep")
        role = "worker" if model.mode == "queue" else "main"
        try:
            await self._backend.scale(model.name, 0, role=role)
        except Exception:
            await self._transition(session, model, (states.SLEEPING,), states.READY)
            raise
        if model.mode == "sync":
            await self._set_route(model.name, states.SLEEPING, model.mode)
        self._emit("model.sleeping", model.name, model.current_digest, states.SLEEPING)

    async def begin_wake(self, session, model: Model) -> None:
        if model.state != states.SLEEPING:
            raise Conflict(f"model {model.name!r} is {model.state}; only sleeping models can wake")
        if not await self._transition(session, model, (states.SLEEPING,), states.STARTING):
            raise Conflict(f"model {model.name!r} is {model.state}; only sleeping models can wake")

    def spawn_wake(self, model_id: int) -> asyncio.Task:
        return self._spawn(self.run_wake(model_id), model_id)

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
                await self._set_route(name, states.READY, mode)
                await self._update(model_id, state=states.READY, state_detail=None)
            except Exception as exc:
                if not isinstance(exc, (DeployFailed, BackendError)):
                    logger.exception("Unexpected error while waking %s", name)
                reason = f"wake failed: {exc}"
                if mode == "queue":
                    # the API still takes jobs; stop the broken worker and let the scaler retry after a cooldown
                    try:
                        await self._backend.scale(name, 0, role="worker")
                    except Exception:
                        logger.warning("Could not stop the worker of %s after a failed start", name)
                    await self._update(model_id, state=states.SLEEPING, state_detail=reason)
                else:
                    await self._update(model_id, state=states.FAILED, state_detail=reason)
                    try:
                        await self._set_route(name, "unavailable", mode)
                    except Exception:
                        logger.warning("Could not mark %s unavailable", name)
                self._emit("deploy.failed", name, digest, states.SLEEPING if mode == "queue" else states.FAILED,
                           reason=reason, rolled_back=False)
                return
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
            await self.redeploy(session, model, clear_failed=False)
            return True
        model.state_detail = "config changes apply on the next deploy"
        await session.flush()
        return False
