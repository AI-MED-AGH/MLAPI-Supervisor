from enum import Enum

from pydantic import BaseModel, Field


class InstanceStatus(str, Enum):
    running = "running"
    stopped = "stopped"
    error = "error"


class Instance(BaseModel):
    id: str
    name: str
    status: InstanceStatus
    model_name: str
    endpoint: str | None = None


class InstanceCreate(BaseModel):
    name: str = Field(..., description="Human-readable name for the model instance")
    model_name: str = Field(..., description="Name of the ML model to load")
    endpoint: str | None = Field(None, description="Optional endpoint URL for the instance")
