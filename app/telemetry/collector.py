import asyncio
import logging
import time

from app.registry import states
from app.registry.queries import list_models
from app.telemetry import store

logger = logging.getLogger(__name__)

# states in which some workload of the model may be running (a sleeping queue model still has its API up)
SAMPLED = (states.DEPLOYING, states.STARTING, states.READY, states.SLEEPING, states.WAITING_FOR_GPU)
PRUNE_EVERY = 300


class UsageCollector:
    """Measures what every model is using and keeps the multi-resolution history."""

    def __init__(self, *, sessionmaker, backend, clock=time.time):
        self._sm = sessionmaker
        self._backend = backend
        self._clock = clock
        self._last_prune = 0.0

    async def _read(self, name: str):
        try:
            return await self._backend.usage(name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Could not measure %s: %s", name, exc)
            return None

    async def tick(self) -> None:
        now = self._clock()
        async with self._sm() as session:
            names = [m.name for m in await list_models(session) if m.state in SAMPLED]
        readings = await asyncio.gather(*(self._read(n) for n in names))
        async with self._sm() as session:
            for name, usage in zip(names, readings):
                if usage is not None:
                    await store.record(session, name, usage, now)
            await store.rollup(session, now)
            if now - self._last_prune >= PRUNE_EVERY:
                await store.prune(session, now)
                self._last_prune = now
            await session.commit()
