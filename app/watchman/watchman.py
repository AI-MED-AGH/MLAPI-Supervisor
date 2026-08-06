import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.watchman.database import get_session
from app.watchman.queries import insert_new_subscription
from app.watchman.schemas import SubscriptionSchema

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

watchmanRouter = APIRouter()

@watchmanRouter.post("/", status_code=status.HTTP_201_CREATED)
async def subscribe(subscription: SubscriptionSchema, session: Annotated[AsyncSession, Depends(get_session)]):
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
        logger.info(f"Added subscription: {subscription}\n")
