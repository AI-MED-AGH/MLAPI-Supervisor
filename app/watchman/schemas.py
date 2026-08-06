from pydantic import BaseModel


class SubscriptionSchema(BaseModel):
    webhook_url: str
    event_types: list[str]
