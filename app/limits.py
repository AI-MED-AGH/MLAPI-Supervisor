"""Request limits the Router enforces. Admins set them on API keys and models; model authors may also declare them in
image labels (clamped, see `effective_model_limits`).

A limit that is absent means "the Router's default"; 0 means unlimited."""
from typing import Any

# keep in step with ML-API/src/limits.py
KEY_LIMIT_FIELDS = ("rpm", "poll_rpm", "jobs_per_day", "max_outstanding_jobs")
MODEL_LIMIT_FIELDS = ("rpm_per_key", "rpm_total", "max_concurrency", "max_pending")
MAX_LIMIT = 10_000_000


def validate_limits(raw: Any, fields: tuple[str, ...]) -> dict[str, int]:
    """Returns a clean copy, or raises ValueError. Unknown names are an error: a typo must not silently do nothing."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("limits must be an object")
    clean: dict[str, int] = {}
    for name, value in raw.items():
        if name not in fields:
            raise ValueError(f"unknown limit {name!r}; known: {', '.join(fields)}")
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_LIMIT:
            raise ValueError(f"limit {name!r} must be an integer between 0 and {MAX_LIMIT} (0 = unlimited)")
        clean[name] = value
    return clean


_CEILING = {
    "rpm_per_key": "limit_ceiling_rpm_per_key",
    "rpm_total": "limit_ceiling_rpm_total",
    "max_concurrency": "limit_ceiling_concurrency",
    "max_pending": "limit_ceiling_max_pending",
}
ROUTER_FIELDS = ("rpm_per_key", "rpm_total", "max_concurrency")


def effective_model_limits(*, declared: dict | None, admin: dict | None, mode: str, settings) -> dict[str, int]:
    """What applies to a model. An admin's value wins outright (including 0 = unlimited and values above the ceiling).
    Otherwise the image's own request is used, but never above the platform ceiling and never unlimited, so a model
    author can protect their model and cannot lift the protection."""
    declared, admin = declared or {}, admin or {}
    result: dict[str, int] = {}
    for name in MODEL_LIMIT_FIELDS:
        if name in admin:
            result[name] = admin[name]
        elif name in declared:
            ceiling = getattr(settings, _CEILING[name])
            value = declared[name]
            result[name] = ceiling if value == 0 or value > ceiling else value
    if mode == "queue":
        result.setdefault("max_pending", settings.default_max_pending)
    else:
        result.pop("max_pending", None)  # only queues have one
    return result


def route_limits(effective: dict) -> dict[str, int]:
    """The part of a model's limits the Router enforces."""
    return {name: effective[name] for name in ROUTER_FIELDS if name in effective}
