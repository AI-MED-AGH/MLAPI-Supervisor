from pydantic import BaseModel
from typing import List

class SubscriptionSchema(BaseModel):
    webhook_url: str    
    event_types: List[str]
