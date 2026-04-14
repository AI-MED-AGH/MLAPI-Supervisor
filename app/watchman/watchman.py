from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.watchman.queries import insert_new_subscription
from app.watchman.database import get_session
from app.watchman.schemas import SubscriptionSchema
import logging

logger = logging.getLogger(__name__)

watchmanRouter = APIRouter()

@watchmanRouter.post("/", status_code=status.HTTP_201_CREATED)
async def subscribe(subscription: SubscriptionSchema, session:AsyncSession = Depends(get_session)):
    try:
        await insert_new_subscription(session, subscription) 
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error occured while subscribing")
    else:
        logger.info(f"\n added subscription: {subscription}")
