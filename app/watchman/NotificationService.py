from app.watchman.queries import get_observers_subscribed_to_event
from sqlalchemy.orm import Session
import httpx
import logging

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

    def notify(self,session:Session, event:str, payload:dict)->None:
        """Sends payload to observers subscribed to an event"""
        observers = get_observers_subscribed_to_event(session, event)
        observers_to_delete = list()
        with httpx.Client() as client:
            for observer in observers:
                try:
                    response = client.post(observer.webhook_url, json=payload, timeout=self.timeout)
                    response.raise_for_status()
                except Exception as e:
                    logger.error(f"\n Observers webhook: {observer.webhook_url} failed with error: {e}")
                    observer.connection_errors_count+=1
                    if observer.connection_errors_count > self.connection_error_max:
                        observers_to_delete.append(observer)
                else:
                    observer.connection_errors_count = 0
                    
        for observer in observers_to_delete:
            logger.info(f"\n deleted observer {repr(observer)} failed {observer.connection_errors_count} times")
            session.delete(observer)
        session.commit()
