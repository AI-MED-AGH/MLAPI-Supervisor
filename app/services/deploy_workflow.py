from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from kubernetes.client.rest import ApiException

from app.services.kubernetes_service import DeploymentSpec

logger = logging.getLogger(__name__)


class DeployFailure(RuntimeError):
    """Raised when a pull-to-deploy workflow fails after retries/rollback."""


@dataclass(frozen=True, slots=True)
class DeployRequest:
    model_id: str
    image: str
    tag: str
    image_reference: str
    replicas: int
    container_port: int
    service_port: int
    env: dict[str, str]
    ghcr_username: str | None = None
    ghcr_token: str | None = None


class _KubernetesServiceLike(Protocol):
    def ensure_registry_pull_secret(self, *, username: str, token: str, registry: str) -> None: ...
    def upsert_model(self, spec: DeploymentSpec) -> dict[str, Any]: ...
    def get_model_status(self, model_id: str) -> dict[str, Any]: ...
    def get_deployment_image(self, model_id: str) -> str | None: ...
    def wait_for_rollout(self, model_id: str, *, timeout_seconds: float, poll_interval: float = ...) -> None: ...
    def patch_deployment_image(self, model_id: str, image: str) -> None: ...


class _StateStoreLike(Protocol):
    def set_model(self, *, model_id: str, image: str, tag: str) -> None: ...


class _LockManagerLike(Protocol):
    def for_model(self, model_id: str) -> threading.Lock: ...


class ModelLockManager:
    def __init__(self) -> None:
        self._registry_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def for_model(self, model_id: str) -> threading.Lock:
        with self._registry_lock:
            lock = self._locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[model_id] = lock
            return lock


class DeployWorkflow:
    def __init__(
        self,
        *,
        kubernetes_service: _KubernetesServiceLike,
        state_store: _StateStoreLike,
        lock_manager: _LockManagerLike,
        registry: str,
        rollout_timeout_seconds: float = 180.0,
        rollout_poll_interval: float = 2.0,
        max_attempts: int = 3,
        backoff_base_seconds: float = 1.0,
        backoff_cap_seconds: float = 10.0,
        sleep_fn=time.sleep,
    ) -> None:
        self._k8s = kubernetes_service
        self._state = state_store
        self._locks = lock_manager
        self._registry = registry
        self._rollout_timeout = rollout_timeout_seconds
        self._rollout_poll_interval = rollout_poll_interval
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._backoff_cap = backoff_cap_seconds
        self._sleep = sleep_fn

    def run(self, request: DeployRequest) -> dict[str, Any]:
        with self._locks.for_model(request.model_id):
            return self._run_locked(request)

    def _run_locked(self, request: DeployRequest) -> dict[str, Any]:
        logger.info(
            "deploy.start model_id=%s image=%s", request.model_id, request.image_reference
        )

        if request.ghcr_username and request.ghcr_token:
            self._call_with_retry(
                "ensure_pull_secret",
                request.model_id,
                lambda: self._k8s.ensure_registry_pull_secret(
                    username=request.ghcr_username,
                    token=request.ghcr_token,
                    registry=self._registry,
                ),
            )

        previous_image = self._safe_read_current_image(request.model_id)

        spec = DeploymentSpec(
            model_id=request.model_id,
            image=request.image_reference,
            replicas=request.replicas,
            container_port=request.container_port,
            service_port=request.service_port,
            env=dict(request.env),
        )

        status = self._call_with_retry(
            "upsert_model", request.model_id, lambda: self._k8s.upsert_model(spec)
        )

        try:
            self._k8s.wait_for_rollout(
                request.model_id,
                timeout_seconds=self._rollout_timeout,
                poll_interval=self._rollout_poll_interval,
            )
        except TimeoutError as exc:
            logger.error(
                "deploy.rollout_timeout model_id=%s image=%s previous=%s",
                request.model_id,
                request.image_reference,
                previous_image,
            )
            self._rollback(request.model_id, previous_image)
            raise DeployFailure(
                f"Rollout for model '{request.model_id}' did not complete: {exc}"
            ) from exc

        self._state.set_model(
            model_id=request.model_id, image=request.image, tag=request.tag
        )
        logger.info(
            "deploy.success model_id=%s image=%s", request.model_id, request.image_reference
        )
        return self._k8s.get_model_status(request.model_id)

    def _safe_read_current_image(self, model_id: str) -> str | None:
        try:
            return self._k8s.get_deployment_image(model_id)
        except ApiException as exc:
            logger.warning(
                "deploy.read_current_image_failed model_id=%s status=%s reason=%s",
                model_id,
                getattr(exc, "status", "?"),
                getattr(exc, "reason", "?"),
            )
            return None

    def _rollback(self, model_id: str, previous_image: str | None) -> None:
        if not previous_image:
            logger.warning(
                "deploy.rollback_skipped model_id=%s reason=no_previous_image", model_id
            )
            return

        try:
            self._k8s.patch_deployment_image(model_id, previous_image)
            logger.info(
                "deploy.rollback_ok model_id=%s image=%s", model_id, previous_image
            )
        except Exception:
            logger.exception(
                "deploy.rollback_failed model_id=%s image=%s", model_id, previous_image
            )

    def _call_with_retry(self, op: str, model_id: str, fn):
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return fn()
            except ApiException as exc:
                if not _is_transient_api_error(exc):
                    raise
                last_exc = exc
            except (ConnectionError, TimeoutError) as exc:
                last_exc = exc

            if attempt == self._max_attempts:
                break
            delay = min(self._backoff_cap, self._backoff_base * (2 ** (attempt - 1)))
            logger.warning(
                "deploy.retry op=%s model_id=%s attempt=%d/%d delay=%.1fs error=%s",
                op,
                model_id,
                attempt,
                self._max_attempts,
                delay,
                last_exc,
            )
            self._sleep(delay)

        assert last_exc is not None
        raise DeployFailure(
            f"{op} for model '{model_id}' failed after {self._max_attempts} attempts: {last_exc}"
        ) from last_exc


def _is_transient_api_error(exc: ApiException) -> bool:
    status = getattr(exc, "status", None)
    if status is None:
        return True
    return status >= 500 or status == 429
