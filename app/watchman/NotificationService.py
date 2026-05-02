from sqlalchemy.exc import SQLAlchemyError
from app.watchman.queries import get_observers_subscribed_to_event, delete_observers
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import logging
import asyncio

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Internal Manager to notify observers subscribed to an event
    connection_error_max is maximum number of times a webhook may fail to respond before deleting the subsciption. Note that the count resets after a correct connection.
    timeout: int timeout of httpx connection
    """
    def __init__(self, connection_error_max:int=5, timeout:int=5):
        self.timeout = timeout
        self.connection_error_max = connection_error_max

    async def notify(self,session:AsyncSession, event:str, payload:dict)->None:
        """Sends payload to observers subscribed to an event"""
        observers = await get_observers_subscribed_to_event(session, event)
        observers_subscribed = list()
        observers_to_delete = list()
        async with httpx.AsyncClient() as client:
            notifications = list()
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
        except (Exception, SQLAlchemyError) as e:
            logger.exception(e)
            await session.rollback()
        else:
            logger.info(f"deleted observers: {observers_to_delete}")
