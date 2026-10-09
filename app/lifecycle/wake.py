import logging
import re

from app.registry import states
from app.registry.errors import Conflict
from app.registry.queries import get_model_by_name

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,38}[a-z0-9])?")


class WakeConsumer:
    """Consumes the Router's `wake` list and starts sleeping sync-mode models."""

    def __init__(self, *, sessionmaker, deployer, redis):
        self._sm = sessionmaker
        self._deployer = deployer
        self._redis = redis

    async def process_one(self, timeout: float = 1) -> str | None:
        item = await self._redis.brpop(["wake"], timeout=timeout)
        if item is None:
            return None
        name = item[1]
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            logger.warning("Dropping malformed wake request")
            return name if isinstance(name, str) else None
        await self._redis.delete(f"wake_pending:{name}")
        async with self._sm() as session:
            model = await get_model_by_name(session, name)
            if model is None or model.mode != "sync" or model.state != states.SLEEPING:
                return name
            try:
                await self._deployer.begin_wake(session, model)
                await session.commit()
            except Conflict:
                await session.rollback()
                return name
            model_id = model.id
        self._deployer.spawn_wake(model_id)
        return name
