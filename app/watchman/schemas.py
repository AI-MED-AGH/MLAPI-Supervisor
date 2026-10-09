from urllib.parse import urlsplit

from pydantic import BaseModel, field_validator


class SubscriptionSchema(BaseModel):
    webhook_url: str
    event_types: list[str]


class SubscriptionRequest(SubscriptionSchema):
    """API-facing subscription: stricter than the internal schema."""

    @field_validator("webhook_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or " " in value:
            raise ValueError("webhook_url must be an http(s) URL with a host")
        return value

    @field_validator("event_types")
    @classmethod
    def _events(cls, value: list[str]) -> list[str]:
        if not value or any(not e.strip() for e in value):
            raise ValueError("event_types must be a non-empty list of non-empty strings")
        return value
