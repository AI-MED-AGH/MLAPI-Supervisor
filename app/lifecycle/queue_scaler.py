import logging
import time

from app.registry import states
from app.registry.errors import Conflict
from app.registry.queries import get_model, list_models

logger = logging.getLogger(__name__)


class QueueScaler:
    """Starts a queue-mode model's worker when jobs wait, stops it after the queue stays empty."""

    def __init__(self, *, sessionmaker, deployer, redis, backend, settings, clock=time.time):
        self._sm = sessionmaker
        self._deployer = deployer
        self._redis = redis
        self._backend = backend
        self._settings = settings
        self._clock = clock
        self._empty_since: dict[str, float] = {}

    async def _work(self, name: str) -> int:
        pending = await self._redis.llen(f"fastmlapi:{name}:pending")
        inflight = 0
        async for key in self._redis.scan_iter(match=f"fastmlapi:{name}:processing:*"):
            inflight += await self._redis.llen(key)
        return pending + inflight

    async def tick(self) -> list[str]:
        async with self._sm() as session:
            queue_models = [
                (m.id, m.name, m.state, (m.config or {}).get("idle_timeout_s", self._settings.default_idle_timeout),
                 m.state_detail or "", m.updated_at or 0)
                for m in await list_models(session)
                if m.mode == "queue" and m.state in (states.READY, states.SLEEPING)
            ]
        changed: list[str] = []
        now = self._clock()
        for model_id, name, state, idle_timeout, detail, updated_at in queue_models:
            if await self._work(name) > 0:
                self._empty_since.pop(name, None)
                recently_failed = detail.startswith("wake failed") and now - updated_at < self._settings.queue_wake_retry_after
                if state == states.SLEEPING and not recently_failed and await self._start_worker(model_id):
                    changed.append(name)
            elif state == states.READY:
                since = self._empty_since.setdefault(name, now)
                if idle_timeout != 0 and now - since > idle_timeout and await self._stop_worker(model_id):
                    self._empty_since.pop(name, None)
                    changed.append(name)
        return changed

    async def _start_worker(self, model_id: int) -> bool:
        async with self._sm() as session:
            model = await get_model(session, model_id)
            try:
                await self._deployer.begin_wake(session, model)
                await session.commit()
            except Conflict:
                await session.rollback()
                return False
        self._deployer.spawn_wake(model_id)
        return True

    async def _stop_worker(self, model_id: int) -> bool:
        async with self._sm() as session:
            model = await get_model(session, model_id)
            try:
                await self._deployer.sleep(session, model)
                await session.commit()
                return True
            except Conflict:
                await session.rollback()
                return False
