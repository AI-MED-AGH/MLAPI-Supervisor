import logging
import time

from app.registry import states
from app.registry.errors import Conflict
from app.registry.queries import get_model, list_models

logger = logging.getLogger(__name__)


class IdleReaper:
    """Puts idle sync-mode models to sleep (queue-mode models are scaled by the QueueScaler)."""

    def __init__(self, *, sessionmaker, deployer, redis, settings, clock=time.time):
        self._sm = sessionmaker
        self._deployer = deployer
        self._redis = redis
        self._settings = settings
        self._clock = clock

    async def tick(self) -> list[str]:
        async with self._sm() as session:
            candidates = [
                (m.id, m.name, (m.config or {}).get("idle_timeout_s", self._settings.default_idle_timeout), m.updated_at or 0)
                for m in await list_models(session)
                if m.state == states.READY and m.mode == "sync"
            ]
        slept: list[str] = []
        for model_id, name, timeout, ready_since in candidates:
            if timeout == 0:
                continue
            raw = await self._redis.get(f"last_active:{name}")
            last_active = int(raw) if raw and str(raw).isdigit() else 0
            # a model that only just became ready must not be put to sleep over old activity
            baseline = max(last_active, ready_since)
            if self._clock() - baseline <= timeout:
                continue
            async with self._sm() as session:
                model = await get_model(session, model_id)
                try:
                    await self._deployer.sleep(session, model)
                    await session.commit()
                    slept.append(name)
                except Conflict:
                    await session.rollback()
        return slept
