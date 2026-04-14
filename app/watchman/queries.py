from typing import Sequence
from sqlalchemy import Select, select
from sqlalchemy.orm import Session
from app.watchman.tables import Observer, Subscription
from app.watchman.schemas import SubscriptionSchema

def get_observers_subscribed_to_event(session: Session, event_name:str)->Sequence[Observer]:
    """get all Observers subscribed to a given event"""
    query:Select = (
            select(Observer)
            .join(Subscription)
            .where(Subscription.event_type == event_name)
        )
    return session.scalars(query).all()
    

def insert_new_subscription(session: Session, subscribtion:SubscriptionSchema)->None:
    """Inserts new subscription"""
    get_observer_query:Select = (
                select(Observer)
                .where(Observer.webhook_url == subscribtion.webhook_url)
            )
     
    observer = session.scalar(get_observer_query)

    if observer is None:
        observer = Observer(webhook_url=subscribtion.webhook_url)
        session.add(observer)
        for event_type in subscribtion.event_types:
            observer.subscriptions.append(Subscription(event_type=event_type))
    else:
        old_event_types = {sub.event_type for sub in observer.subscriptions}
        new_event_types = [e for e in subscribtion.event_types  if e not in old_event_types]
        for event_type in new_event_types:
            observer.subscriptions.append(Subscription(event_type=event_type))

    session.commit()
    session.refresh(observer)
