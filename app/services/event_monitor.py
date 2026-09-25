import asyncio
import logging

from kubernetes.client.rest import ApiException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.schemas import EventPayload

from .kubernetes_service import KubernetesService
from .notification_service import NotificationService

logger = logging.getLogger(__name__)


class EventMonitor:
    """
    Background worker that scans, every interval, k8 cluster via KubernetesService, checks for changed statuses of models
    and sends model snapshot via NotificationService to registered webhooks.
    """

    def __init__(
        self,
        kubernetes_service: KubernetesService,
        notification_service: NotificationService,
    ) -> None:
        self._kubernetes_service = kubernetes_service
        self._notification_service = notification_service
        self._previous_models_status = {}
        self._running = False
        self._task = None

    async def start(self, interval: int) -> None:
        """Starts monitoring task with given interval"""
        self._running = True
        self._task = asyncio.create_task(self._monitor(interval))

    async def _monitor(self, interval: int):
        """Safely runs _loop"""
        try:
            await self._loop(interval)
        except asyncio.CancelledError:
            logger.info("Worker received cancel signal and is shutting down.")
        except Exception:
            logger.exception("Worker crashed with unexpected error.")

    async def _loop(self, interval: int) -> None:
        """Every interval, registers all the new events and then sends them via NotificationService"""
        while self._running:
            try:
                await self._sent_events()
            except ApiException:
                logger.exception("Kubernetes API temporarily unavailable.")
            except Exception:
                logger.exception("Failed to gather cluster status.")
            finally:
                await asyncio.sleep(interval)

    async def _sent_events(self) -> None:
        """Compares the old statuses with the new ones and creates event payload based on model snapshots provided by KubernetesService"""
        cluster_statuses = self._kubernetes_service.get_models_cluster_status()
        for model_data in cluster_statuses:
            model_id = model_data.name
            current_status = model_data.status

            previous_status = self._previous_models_status.get(model_id, "Running")

            if current_status != previous_status:
                async with SessionLocal() as session:
                    await self._notification_service.notify(
                        session,
                        current_status,
                        EventPayload.create(current_status, model_data),
                    )

                self._previous_models_status[model_id] = current_status

    async def stop(self) -> None:
        """Stops the monitoring"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
