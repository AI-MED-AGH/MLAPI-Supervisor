from app.watchman.queries import get_webhooks_subscribed_to_event
from sqlalchemy.orm import Session
import httpx

class NotificationService:
    def __init__(self):
        self.client = httpx.Client()
        self.timeout = 5

    def notify(self,session:Session, event:str, payload)->None:
        observers = get_webhooks_subscribed_to_event(session, event)
        for observer in observers:
            try:
                response = self.client.post(observer.webhook_url, json=payload, timeout=self.timeout)
                response.raise_for_status()
            except httpx.ConnectError or httpx.TimeoutException:
                session.delete(observer)
                
        session.commit()

    def close(self):
        self.client.close()

