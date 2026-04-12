from typing import Sequence, List
from sqlalchemy import Select, select
from sqlalchemy.orm import Session
from app.watchman.tables import Event, Observer
from app.watchman.schemas import SubscriptionSchema


def get_observers_subscribed_to_event(session: Session, event_name:str)->Sequence[Observer]:
    """get all Observers subscribed to a given event"""
    query:Select = (
            select(Observer)
            .join(Observer.events)
            .where(Event.name == event_name)
        )
    return session.scalars(query).all()
    
def events_exist(session:Session, events:List[str])->bool:
    """Checks if all events client wants to subscribe to exist"""
    if len(events) == 0:
        return False

    query:Select = (
            select(Event.name)
            .where(Event.name.in_(events))
            )

    existing_events = set(session.scalars(query).all())

    events_set = set(events)
    if events_set.issubset(existing_events): 
         return True 
    return False
    

def insert_new_subscription(session: Session, subscribtion:SubscriptionSchema)->None:
    """Inserts new subscription"""
    get_observer_query:Select = (
                select(Observer)
                .where(Observer.webhook_url == subscribtion.webhook_url)
            )
    get_events_query:Select = (
                select(Event)
                .where(Event.name.in_(subscribtion.event_types))
            )
     
    events = session.scalars(get_events_query).all()
    observer = session.scalar(get_observer_query)
    if observer is None:
        observer = Observer(webhook_url=subscribtion.webhook_url)
        observer.events.extend(events) 
        session.add(observer)
    else:
        old_events = set(observer.events)
        new_events = [event for event in events if event not in old_events]
        observer.events.extend(new_events)

    session.commit()

