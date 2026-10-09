import asyncio
import logging
import time

from app.watchman.notification_service import NotificationService

logger = logging.getLogger(__name__)


class EventBus:
    """Delivers platform events to webhook observers in the background.

    `emit` never blocks and never raises, so a slow or broken observer cannot hold up a deploy.
    """

    def __init__(self, sessionmaker, notifier=None):
        self._sessionmaker = sessionmaker
        self._notifier = notifier or NotificationService()
        self._tasks: set[asyncio.Task] = set()

    def emit(
        self,
        event: str,
        model: str,
        *,
        digest: str | None = None,
        status: str | None = None,
        **details,
    ) -> None:
        payload = {
            "event": event,
            "model": model,
            "digest": digest,
            "status": status,
            "timestamp": int(time.time()),
            "details": details,
        }
        try:
            task = asyncio.get_running_loop().create_task(self._deliver(event, payload))
        except RuntimeError:
            logger.warning("No running loop; dropping event %s", event)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, event: str, payload: dict) -> None:
        try:
            async with self._sessionmaker() as session:
                await self._notifier.notify(session, event, payload)
        except Exception:
            logger.exception("Failed to deliver event %s", event)

    async def drain(self) -> None:
        """Wait for in-flight deliveries (used in tests and on shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
