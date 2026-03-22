from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session
from app.watchman.queries import events_exist, insert_new_subscription
from app.watchman.database import get_session
from app.watchman.schemas import SubscriptionSchema

watchmanRouter = APIRouter()

@watchmanRouter.post("/", status_code=201)
def subscribe(subscription: SubscriptionSchema, session:Session = Depends(get_session)):
    if not events_exist(session, subscription.event_types):
        raise HTTPException(status_code=404, detail="Event doesn't exist")
    insert_new_subscription(session, subscription) 






