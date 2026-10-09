import asyncio
import logging

logger = logging.getLogger(__name__)


class Background:
    """Owns the long-running supervisor loops (reaper, wake consumer, queue scaler, reconciler, poller)."""

    def __init__(self, *, reaper, scaler, reconciler, wake, poller, settings):
        self._tasks: list[asyncio.Task] = []
        self._loops = [
            ("idle-reaper", reaper.tick, settings.reaper_interval),
            ("queue-scaler", scaler.tick, settings.queue_poll_interval),
            ("reconciler", reconciler.tick, settings.reconcile_interval),
        ]
        if poller is not None:
            self._loops.append(("ghcr-poller", poller.tick, settings.poll_interval))
        self._wake = wake
        self._poller = poller

    async def _periodic(self, name: str, tick, interval: float) -> None:
        while True:
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background loop %s failed; continuing", name)
            await asyncio.sleep(interval)

    async def _wake_loop(self) -> None:
        while True:
            try:
                await self._wake.process_one(timeout=1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Wake consumer failed; continuing")
                await asyncio.sleep(1)

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        for name, tick, interval in self._loops:
            self._tasks.append(loop.create_task(self._periodic(name, tick, interval), name=name))
        self._tasks.append(loop.create_task(self._wake_loop(), name="wake-consumer"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._poller is not None and hasattr(self._poller, "aclose"):
            await self._poller.aclose()


def build_background(services, state) -> "Background":
    """Assembles the loops from the shared services (state = app.state)."""
    from app.lifecycle.queue_scaler import QueueScaler
    from app.lifecycle.reaper import IdleReaper
    from app.lifecycle.reconciler import Reconciler
    from app.lifecycle.wake import WakeConsumer

    sm, deployer, redis, settings = state.sessionmaker, services.deployer, state.redis, state.settings
    poller = None
    if settings.ghcr_org and settings.ghcr_token:
        from app.poller.service import build_poller

        poller = build_poller(services, state)
    return Background(
        reaper=IdleReaper(sessionmaker=sm, deployer=deployer, redis=redis, settings=settings),
        scaler=QueueScaler(sessionmaker=sm, deployer=deployer, redis=redis, backend=services.backend, settings=settings),
        reconciler=Reconciler(sessionmaker=sm, deployer=deployer, redis=redis, backend=services.backend, probe=services.probe),
        wake=WakeConsumer(sessionmaker=sm, deployer=deployer, redis=redis),
        poller=poller,
        settings=settings,
    )
