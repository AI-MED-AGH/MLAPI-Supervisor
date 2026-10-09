from dataclasses import dataclass

from app.cluster.backend import BackendError, ModelSpec, RuntimeStatus


@dataclass
class _Entry:
    spec: ModelSpec
    replicas: int = 1
    worker_replicas: int = 0
    polls_left: int = 0


class FakeBackend:
    """In-memory ClusterBackend for tests: scriptable readiness, GPU slots and failures."""

    def __init__(self, *, ready_after: int = 0, gpu_slots: int | None = None):
        self.ready_after = ready_after
        self.gpu_slots = gpu_slots  # None = unlimited
        self.fail_apply: Exception | None = None
        self.never_ready: set[str] = set()  # image refs whose workloads never become ready
        self.models: dict[str, _Entry] = {}
        self.calls: list[tuple] = []

    async def apply_model(self, spec: ModelSpec) -> None:
        self.calls.append(("apply_model", spec.name, spec.image))
        if self.fail_apply is not None:
            raise self.fail_apply
        previous = self.models.get(spec.name)
        self.models[spec.name] = _Entry(
            spec=spec,
            replicas=1,
            worker_replicas=previous.worker_replicas if previous else 0,
            polls_left=self.ready_after,
        )

    async def scale(self, name: str, replicas: int, role: str = "main") -> None:
        self.calls.append(("scale", name, replicas, role))
        entry = self.models.get(name)
        if entry is None:
            raise BackendError(f"unknown model {name}")
        if role == "worker":
            entry.worker_replicas = replicas
        else:
            entry.replicas = replicas
        if replicas > 0:
            entry.polls_left = self.ready_after

    async def delete_model(self, name: str, *, keep_cache: bool = False) -> None:
        self.calls.append(("delete_model", name, keep_cache))
        self.models.pop(name, None)

    async def status(self, name: str) -> RuntimeStatus:
        entry = self.models.get(name)
        if entry is None:
            return RuntimeStatus(exists=False)
        wants_gpu = entry.spec.resources.gpu
        gpu_role_running = (
            entry.worker_replicas > 0 if entry.spec.mode == "queue" else entry.replicas > 0
        )
        if wants_gpu and gpu_role_running and self.gpu_slots is not None and self.gpu_slots < 1:
            return RuntimeStatus(
                exists=True,
                replicas=entry.replicas,
                worker_replicas=entry.worker_replicas,
                pending_reason="unschedulable: no free GPU slice",
            )
        if entry.spec.image in self.never_ready:
            return RuntimeStatus(exists=True, replicas=entry.replicas, worker_replicas=entry.worker_replicas)
        if entry.replicas > 0 and entry.polls_left > 0:
            entry.polls_left -= 1
            return RuntimeStatus(exists=True, replicas=entry.replicas, worker_replicas=entry.worker_replicas)
        return RuntimeStatus(
            exists=True,
            ready=entry.replicas > 0,
            replicas=entry.replicas,
            worker_replicas=entry.worker_replicas,
        )

    async def endpoint(self, name: str) -> str:
        return f"http://fake-{name}:8000"

    async def list_models(self) -> list[str]:
        return sorted(self.models)
