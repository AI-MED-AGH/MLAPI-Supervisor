from typing import Sequence
from sqlalchemy import Select, select 
from sqlalchemy.ext.asyncio import AsyncSession
from app.watchman.tables import Observer, Subscription
from app.watchman.schemas import SubscriptionSchema
from sqlalchemy.orm import selectinload
import logging

logger = logging.getLogger(__name__)

async def delete_observers(session:AsyncSession, observers:list[Observer])->None:
    """Deletes observers specified in an observers list"""
    for observer in observers:
        print(f"deleting{observer}")
        await session.delete(observer)
    

async def get_observers_subscribed_to_event(session: AsyncSession, event_name:str)->Sequence[Observer]:
    """get all Observers subscribed to a given event"""
    query:Select = (
            select(Observer)
            .join(Subscription)
            .where(Subscription.event_type == event_name)
        )
    return (await session.scalars(query)).all()
    

async def insert_new_subscription(session: AsyncSession, subscribtion:SubscriptionSchema)->None:
    """Inserts new subscription"""
    get_observer_query:Select = (
                select(Observer)
                .where(Observer.webhook_url == subscribtion.webhook_url)
                .options(selectinload(Observer.subscriptions))
            )
     
    observer = await session.scalar(get_observer_query)

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

    await session.commit()
    await session.refresh(observer)
