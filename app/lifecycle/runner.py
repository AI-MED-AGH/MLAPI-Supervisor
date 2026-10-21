import asyncio
import logging

logger = logging.getLogger(__name__)


class Background:
    """Owns the long-running supervisor loops (reaper, wake consumer, queue scaler, reconciler, poller)."""

    def __init__(self, *, reaper, scaler, reconciler, wake, poller, settings, collector=None):
        self._tasks: list[asyncio.Task] = []
        self._loops = [
            ("idle-reaper", reaper.tick, settings.reaper_interval),
            ("queue-scaler", scaler.tick, settings.queue_poll_interval),
            ("reconciler", reconciler.tick, settings.reconcile_interval),
        ]
        if poller is not None:
            self._loops.append(("ghcr-poller", poller.tick, settings.poll_interval))
        if collector is not None and settings.telemetry_interval > 0:
            self._loops.append(("usage-collector", collector.tick, settings.telemetry_interval))
        self._fixed_rate = {"usage-collector"}  # measurements are only comparable when evenly spaced
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

    async def _fixed_rate_loop(self, name: str, tick, interval: float) -> None:
        """Like _periodic, but the interval is start-to-start, so a slow tick does not stretch the period."""
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background loop %s failed; continuing", name)
            await asyncio.sleep(max(0.0, interval - (loop.time() - started)))

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
            run = self._fixed_rate_loop if name in self._fixed_rate else self._periodic
            self._tasks.append(loop.create_task(run(name, tick, interval), name=name))
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
    from app.telemetry.collector import UsageCollector

    sm, deployer, redis, settings = state.sessionmaker, services.deployer, state.redis, state.settings
    poller = None
    if settings.ghcr_org and settings.ghcr_token:
        from app.poller.service import build_poller

        poller = build_poller(services, state)
    return Background(
        reaper=IdleReaper(sessionmaker=sm, deployer=deployer, redis=redis, settings=settings),
        scaler=QueueScaler(sessionmaker=sm, deployer=deployer, redis=services.queue_redis or redis, backend=services.backend,
                           settings=settings),
        reconciler=Reconciler(sessionmaker=sm, deployer=deployer, redis=redis, backend=services.backend, probe=services.probe,
                              queue_access=services.queue_access, settings=settings),
        wake=WakeConsumer(sessionmaker=sm, deployer=deployer, redis=redis),
        poller=poller,
        settings=settings,
        collector=UsageCollector(sessionmaker=sm, backend=services.backend),
    )
