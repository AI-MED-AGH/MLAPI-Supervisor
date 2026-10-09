import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.watchman.database import get_session
from app.watchman.queries import (
    get_observer,
    insert_new_subscription,
    list_observers,
)
from app.watchman.schemas import SubscriptionRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

watchmanRouter = APIRouter()

@watchmanRouter.post("/", status_code=status.HTTP_201_CREATED)
async def subscribe(subscription: SubscriptionRequest, session: Annotated[AsyncSession, Depends(get_session)]):
    """
    Subscribes to a certain event types, in order to recive notifications, at a provided webhook url.

    Raises:
        HTTPException: If an error occurs while subscribing, a 500 Internal Server Error is raised.

    Returns:
        Returns a 201 Created status code on success.
    """
    try:
        await insert_new_subscription(session, subscription)
    except (Exception, SQLAlchemyError):
        await session.rollback()
        logger.exception("Error occurred while subscribing")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error occurred while subscribing")
    else:
        await session.commit()
        logger.info("Added a webhook subscription for %d event type(s)", len(subscription.event_types))


@watchmanRouter.get("/")
async def list_all(session: Annotated[AsyncSession, Depends(get_session)]):
    """Lists observers and the events each one is subscribed to."""
    return [
        {
            "id": o.id,
            "webhook_url": o.webhook_url,
            "event_types": sorted(s.event_type for s in o.subscriptions),
            "connection_errors_count": o.connection_errors_count,
        }
        for o in await list_observers(session)
    ]


@watchmanRouter.delete("/{observer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(observer_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    observer = await get_observer(session, observer_id)
    if observer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer not found")
    await session.delete(observer)
    await session.commit()
