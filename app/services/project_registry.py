from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    image: str
    ghcr_username: str
    ghcr_token: str
    default_tag: str = "latest"
    replicas: int = 1
    container_port: int | None = None
    service_port: int | None = None
    env: Mapping[str, str] = field(default_factory=dict)


class ProjectRegistry(Protocol):
    def get(self, project_id: str) -> Project | None: ...


class InMemoryProjectRegistry:
    def __init__(self, projects: Iterable[Project] = ()) -> None:
        self._lock = threading.Lock()
        self._projects: dict[str, Project] = {}
        for project in projects:
            self._projects[project.project_id] = project

    def get(self, project_id: str) -> Project | None:
        with self._lock:
            return self._projects.get(project_id)

    def upsert(self, project: Project) -> None:
        with self._lock:
            self._projects[project.project_id] = project

    def delete(self, project_id: str) -> bool:
        with self._lock:
            return self._projects.pop(project_id, None) is not None

    @classmethod
    def from_environment(cls, env_var: str = "ML_SUPERVISOR_SEED_PROJECTS") -> "InMemoryProjectRegistry":
        raw = os.getenv(env_var, "").strip()
        if not raw:
            return cls()

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{env_var} is not valid JSON: {exc}") from exc

        if not isinstance(payload, list):
            raise ValueError(f"{env_var} must be a JSON array of project objects")

        return cls(_parse_project(item) for item in payload)


def _parse_project(item: object) -> Project:
    if not isinstance(item, dict):
        raise ValueError("each seeded project must be a JSON object")

    required = ("project_id", "image", "ghcr_username", "ghcr_token")
    missing = [key for key in required if not str(item.get(key, "")).strip()]
    if missing:
        raise ValueError(f"seeded project is missing required fields: {', '.join(missing)}")

    env = item.get("env", {}) or {}
    if not isinstance(env, dict):
        raise ValueError("project 'env' must be a JSON object")

    return Project(
        project_id=str(item["project_id"]).strip(),
        image=str(item["image"]).strip(),
        ghcr_username=str(item["ghcr_username"]).strip(),
        ghcr_token=str(item["ghcr_token"]).strip(),
        default_tag=str(item.get("default_tag", "latest")).strip() or "latest",
        replicas=int(item.get("replicas", 1)),
        container_port=_optional_int(item.get("container_port")),
        service_port=_optional_int(item.get("service_port")),
        env={str(k): str(v) for k, v in env.items()},
    )


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
