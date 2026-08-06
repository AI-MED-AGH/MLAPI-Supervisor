import asyncio
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.watchman.queries import delete_observers, get_observers_subscribed_to_event

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Internal Manager that notifies observers subscribed to events

    connection_error_max is maximum number of times a webhook may fail to respond before deleting the subsciption. Note that the count resets after a correct connection.
    timeout: duration after which the httpx connection times out
    """
    def __init__(self, connection_error_max:int=5, timeout:int=5):
        self.timeout = timeout
        self.connection_error_max = connection_error_max

    async def notify(self,session:AsyncSession, event:str, payload:dict)->None:
        """
        Sends payload to observers subscribed to an event.
        If number of connection errors exceeds connection_error_max, the observer is deleted from the database.
        If the observer responds successfully, the connection error count is reset to 0.
        If we failed deleting observers, we rollback the transaction and log the error, then try again next time.
        """
        observers = await get_observers_subscribed_to_event(session, event)
        observers_subscribed = []
        observers_to_delete = []
        async with httpx.AsyncClient() as client:
            notifications = []
            for observer in observers:
                notification = client.post(observer.webhook_url, json=payload, timeout=self.timeout)
                notifications.append(notification)
                observers_subscribed.append(observer)

            results = await asyncio.gather(*notifications, return_exceptions=True)
            for result, observer in zip(results, observers_subscribed):
                if isinstance(result, Exception) or (isinstance(result, httpx.Response) and not result.is_success):
                    observer.connection_errors_count+=1
                    if observer.connection_errors_count > self.connection_error_max:
                        observers_to_delete.append(observer)
                else:
                    observer.connection_errors_count = 0

        try:
            await delete_observers(session, observers_to_delete)
            await session.commit()
        except (SQLAlchemyError, Exception):
            logger.exception("Error occured while deleting observers")
            await session.rollback()
        else:
            logger.info(f"deleted observers: {observers_to_delete}")
