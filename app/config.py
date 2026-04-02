from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    k8s_namespace: str = "default"
    k8s_service_type: Literal["LoadBalancer", "NodePort", "ClusterIP"] = "LoadBalancer"
    ghcr_registry: str = "ghcr.io"
    ghcr_username: str | None = None
    ghcr_token: str | None = None
    image_pull_secret_name: str = "ghcr-pull-secret"
    enable_polling: bool = False
    poll_interval_seconds: int = Field(default=60, ge=5)
    watched_images: list[str] = Field(default_factory=list)
    default_container_port: int = Field(default=8000, ge=1, le=65535)
    default_service_port: int = Field(default=80, ge=1, le=65535)
    poll_state_file: str = "/tmp/mlapi-supervisor-state.json"

    @field_validator("watched_images", mode="before")
    @classmethod
    def parse_watched_images(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise TypeError("watched_images must be a comma-separated string or list of strings")

    @classmethod
    def from_environment(cls) -> "Settings":
        raw: dict[str, Any] = {
            "k8s_namespace": os.getenv("ML_SUPERVISOR_K8S_NAMESPACE", "default"),
            "k8s_service_type": os.getenv("ML_SUPERVISOR_K8S_SERVICE_TYPE", "LoadBalancer"),
            "ghcr_registry": os.getenv("ML_SUPERVISOR_GHCR_REGISTRY", "ghcr.io"),
            "ghcr_username": os.getenv("ML_SUPERVISOR_GHCR_USERNAME"),
            "ghcr_token": os.getenv("ML_SUPERVISOR_GHCR_TOKEN"),
            "image_pull_secret_name": os.getenv(
                "ML_SUPERVISOR_IMAGE_PULL_SECRET_NAME", "ghcr-pull-secret"
            ),
            "enable_polling": os.getenv("ML_SUPERVISOR_ENABLE_POLLING", "false"),
            "poll_interval_seconds": os.getenv("ML_SUPERVISOR_POLL_INTERVAL_SECONDS", "60"),
            "watched_images": os.getenv("ML_SUPERVISOR_WATCHED_IMAGES", ""),
            "default_container_port": os.getenv("ML_SUPERVISOR_DEFAULT_CONTAINER_PORT", "8000"),
            "default_service_port": os.getenv("ML_SUPERVISOR_DEFAULT_SERVICE_PORT", "80"),
            "poll_state_file": os.getenv(
                "ML_SUPERVISOR_POLL_STATE_FILE", "/tmp/mlapi-supervisor-state.json"
            ),
        }

        for key in ("ghcr_username", "ghcr_token"):
            value = raw.get(key)
            if isinstance(value, str) and not value.strip():
                raw[key] = None

        return cls.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()