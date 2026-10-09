import re
from dataclasses import dataclass

from app.registry.resources import ResourceError, Resources, parse_cpu, parse_quantity

NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,38}[a-z0-9])?")

DEFAULT_CPU = "1"
DEFAULT_MEMORY = "2Gi"
DEFAULT_DISK = "5Gi"


class NotAModel(Exception):
    """The image does not carry mlapi.model=true."""


class LabelError(ValueError):
    """The image claims to be a model but its labels are invalid."""


@dataclass(frozen=True)
class ModelLabels:
    name: str
    mode: str
    resources: Resources


def parse_labels(labels: dict[str, str] | None, package_name: str) -> ModelLabels:
    labels = labels or {}
    if labels.get("mlapi.model") != "true":
        raise NotAModel()

    name = labels.get("mlapi.model.name") or package_name.rsplit("/", 1)[-1]
    if not NAME_RE.fullmatch(name):
        raise LabelError(f"invalid model name: {name!r}")

    mode = labels.get("mlapi.mode", "sync")
    if mode not in ("sync", "queue"):
        raise LabelError(f"mlapi.mode must be 'sync' or 'queue', got {mode!r}")

    gpu_raw = labels.get("mlapi.gpu", "false")
    if gpu_raw not in ("true", "false"):
        raise LabelError(f"mlapi.gpu must be 'true' or 'false', got {gpu_raw!r}")

    try:
        resources = Resources(
            cpu_m=parse_cpu(labels.get("mlapi.cpu", DEFAULT_CPU)),
            memory_bytes=parse_quantity(labels.get("mlapi.memory", DEFAULT_MEMORY)),
            gpu=gpu_raw == "true",
            disk_bytes=parse_quantity(labels.get("mlapi.disk", DEFAULT_DISK)),
        )
    except ResourceError as exc:
        raise LabelError(str(exc)) from exc
    return ModelLabels(name=name, mode=mode, resources=resources)
