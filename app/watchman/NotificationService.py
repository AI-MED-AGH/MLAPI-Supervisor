from app.watchman.queries import get_observers_subscribed_to_event
from sqlalchemy.orm import Session
import httpx

class NotificationService:
    """
    Internal utility to notify observers subscribed to an event
    connection_error_max is maximum number of times a webhook may fail to respond before deleting the subsciption. Note that the count resets after a correct connection.
    """
    def __init__(self, connection_error_max:int=5):
        self.client = httpx.Client()
        self.timeout = 5
        self.connection_error_max = connection_error_max

    def notify(self,session:Session, event:str, payload)->None:
        """Sends payload to observers subscribed to an event"""
        observers = get_observers_subscribed_to_event(session, event)
        for observer in observers:
            try:
                response = self.client.post(observer.webhook_url, json=payload, timeout=self.timeout)
                response.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                print(f"\n[DEBUG] Webhook {observer.webhook_url} failed with error: {repr(e)}")
                observer.connection_errors_count+=1
                if observer.connection_errors_count > self.connection_error_max:
                    session.delete(observer)
            else:
                observer.connection_errors_count = 0
                
        session.commit()

    def close(self):
        self.client.close()

