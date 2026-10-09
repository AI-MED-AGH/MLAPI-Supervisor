from dataclasses import dataclass, field
from typing import Protocol

from app.registry.resources import Resources


class BackendError(Exception):
    """A cluster backend operation failed."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    image: str  # reference pinned by digest (or a local image id)
    mode: str  # sync | queue
    resources: Resources  # APPROVED resources (for queue mode: the worker's)
    env: dict[str, str] = field(default_factory=dict)
    secret_refs: tuple[str, ...] = ()
    queue_url: str | None = None  # queue mode: per-model Redis URL
    max_job_seconds: int = 600  # queue mode: worker termination grace
    api_resources: Resources | None = None  # queue mode: fixed allotment for the API role


@dataclass(frozen=True)
class RuntimeStatus:
    exists: bool
    ready: bool = False  # sync: the model container; queue: the API container
    replicas: int = 0  # running replicas of the main workload (sync model / queue API)
    worker_replicas: int = 0  # queue mode only
    pending_reason: str | None = None  # e.g. "unschedulable: no free GPU slice"
    restarts: int = 0


class ClusterBackend(Protocol):
    async def apply_model(self, spec: ModelSpec) -> None: ...

    async def scale(self, name: str, replicas: int, role: str = "main") -> None:
        """role is 'main' (sync model or queue API) or 'worker' (queue mode)."""

    async def delete_model(self, name: str, *, keep_cache: bool = False) -> None: ...

    async def status(self, name: str) -> RuntimeStatus: ...

    async def endpoint(self, name: str) -> str:
        """URL the Router should use to reach the model (its API in queue mode)."""

    async def list_models(self) -> list[str]:
        """Names of models this backend currently manages."""
