from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_MODEL_ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_model_id(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if not _MODEL_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "model_id must be a valid DNS-1123 label (lowercase alphanumeric and '-')."
        )
    return normalized


def build_image_reference(image: str, tag: str, registry: str) -> str:
    cleaned_image = image.strip()
    if not cleaned_image:
        raise ValueError("image cannot be empty")

    if "/" not in cleaned_image:
        raise ValueError("image must include owner/repository")

    registry_prefix = f"{registry.strip().rstrip('/')}/"
    image_first_segment = cleaned_image.split("/", 1)[0]

    if cleaned_image.startswith(registry_prefix) or "." in image_first_segment:
        with_registry = cleaned_image
    else:
        with_registry = f"{registry_prefix}{cleaned_image}"

    last_segment = with_registry.rsplit("/", 1)[-1]
    if ":" in last_segment:
        return with_registry

    cleaned_tag = tag.strip() or "latest"
    return f"{with_registry}:{cleaned_tag}"


class ModelDeploymentRequest(BaseModel):
    model_id: str = Field(..., description="Unique model identifier used as Kubernetes object prefix.")
    image: str = Field(..., description="Image repository path, e.g. owner/model-api.")
    tag: str = Field(default="latest", description="Image tag to deploy.")
    replicas: int = Field(default=1, ge=1, le=10)
    container_port: int | None = Field(default=None, ge=1, le=65535)
    service_port: int | None = Field(default=None, ge=1, le=65535)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        return normalize_model_id(value)

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("image cannot be empty")
        return cleaned

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("tag cannot be empty")
        return cleaned


class ModelDeploymentResponse(BaseModel):
    model_id: str
    deployment_name: str
    service_name: str
    image: str
    namespace: str
    service_type: str
    external_endpoints: list[str]
    message: str


class ProjectPullRequest(BaseModel):
    tag: str | None = Field(default=None, description="Optional tag override; otherwise uses project default or GHCR latest.")

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ModelDeleteResponse(BaseModel):
    model_id: str
    deleted: bool


class ModelStatusResponse(BaseModel):
    model_id: str
    deployment_name: str
    service_name: str
    image: str | None = None
    namespace: str
    phase: str
    desired_replicas: int
    ready_replicas: int
    available_replicas: int
    service_type: str
    external_endpoints: list[str]


class ModelListItem(BaseModel):
    model_id: str
    image: str | None = None
    deployment_name: str
    service_name: str
    phase: str
    ready_replicas: int
    available_replicas: int
    external_endpoints: list[str]


class ModelListResponse(BaseModel):
    items: list[ModelListItem]