from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from kubernetes.client.rest import ApiException

from app.services import (
    DeployFailure,
    DeployRequest,
    DeployWorkflow,
    InMemoryProjectRegistry,
    ModelLockManager,
    Project,
)
from app.services.kubernetes_service import DeploymentSpec


class RecordingK8s:
    def __init__(self) -> None:
        self.deployments: dict[str, dict[str, Any]] = {}
        self.patches: list[tuple[str, str]] = []
        self.pull_secret_calls: list[tuple[str, str, str]] = []
        self.wait_behavior: list[str] = []  # "ok" or "timeout"
        self.upsert_errors: list[Exception] = []

    def ensure_registry_pull_secret(self, *, username: str, token: str, registry: str) -> None:
        self.pull_secret_calls.append((username, token, registry))

    def upsert_model(self, spec: DeploymentSpec) -> dict[str, Any]:
        if self.upsert_errors:
            exc = self.upsert_errors.pop(0)
            raise exc
        self.deployments[spec.model_id] = {
            "image": spec.image,
            "deployment_name": f"model-{spec.model_id}",
            "service_name": f"model-{spec.model_id}-svc",
            "namespace": "ns",
            "phase": "ready",
            "desired_replicas": spec.replicas,
            "ready_replicas": spec.replicas,
            "available_replicas": spec.replicas,
            "service_type": "LoadBalancer",
            "external_endpoints": [],
        }
        return self.get_model_status(spec.model_id)

    def get_model_status(self, model_id: str) -> dict[str, Any]:
        return dict(self.deployments[model_id], model_id=model_id)

    def get_deployment_image(self, model_id: str) -> str | None:
        entry = self.deployments.get(model_id)
        return entry["image"] if entry else None

    def wait_for_rollout(self, model_id: str, *, timeout_seconds: float, poll_interval: float = 2.0) -> None:
        if not self.wait_behavior:
            return
        outcome = self.wait_behavior.pop(0)
        if outcome == "timeout":
            raise TimeoutError("rollout timed out")

    def patch_deployment_image(self, model_id: str, image: str) -> None:
        self.patches.append((model_id, image))
        if model_id in self.deployments:
            self.deployments[model_id]["image"] = image


class FakeStateStore:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, str]] = {}

    def set_model(self, *, model_id: str, image: str, tag: str) -> None:
        self.models[model_id] = {"image": image, "tag": tag}


def _make_request(model_id: str = "demo", image: str = "owner/app", tag: str = "1.0.0") -> DeployRequest:
    return DeployRequest(
        model_id=model_id,
        image=image,
        tag=tag,
        image_reference=f"ghcr.io/{image}:{tag}",
        replicas=1,
        container_port=8000,
        service_port=80,
        env={},
        ghcr_username="ai-med-agh",
        ghcr_token="secret",
    )


def _workflow(k8s: RecordingK8s, state: FakeStateStore, **overrides: Any) -> DeployWorkflow:
    defaults = {
        "kubernetes_service": k8s,
        "state_store": state,
        "lock_manager": ModelLockManager(),
        "registry": "ghcr.io",
        "rollout_timeout_seconds": 5.0,
        "rollout_poll_interval": 0.01,
        "max_attempts": 3,
        "backoff_base_seconds": 0,
        "backoff_cap_seconds": 0,
        "sleep_fn": lambda _s: None,
    }
    defaults.update(overrides)
    return DeployWorkflow(**defaults)


def test_happy_path_writes_state_after_rollout():
    k8s = RecordingK8s()
    state = FakeStateStore()
    workflow = _workflow(k8s, state)

    result = workflow.run(_make_request())

    assert result["image"] == "ghcr.io/owner/app:1.0.0"
    assert state.models["demo"] == {"image": "owner/app", "tag": "1.0.0"}
    assert k8s.pull_secret_calls == [("ai-med-agh", "secret", "ghcr.io")]
    assert k8s.patches == []


def test_rollout_timeout_rolls_back_and_leaves_state_untouched():
    k8s = RecordingK8s()
    k8s.deployments["demo"] = {
        "image": "ghcr.io/owner/app:0.9.0",
        "deployment_name": "model-demo",
        "service_name": "model-demo-svc",
        "namespace": "ns",
        "phase": "ready",
        "desired_replicas": 1,
        "ready_replicas": 1,
        "available_replicas": 1,
        "service_type": "LoadBalancer",
        "external_endpoints": [],
    }
    k8s.wait_behavior = ["timeout"]
    state = FakeStateStore()
    workflow = _workflow(k8s, state)

    with pytest.raises(DeployFailure):
        workflow.run(_make_request())

    assert k8s.patches == [("demo", "ghcr.io/owner/app:0.9.0")]
    assert state.models == {}


def test_rollout_timeout_skips_rollback_when_no_previous_image():
    k8s = RecordingK8s()
    k8s.wait_behavior = ["timeout"]
    state = FakeStateStore()

    class FirstDeployK8s(RecordingK8s):
        def get_deployment_image(self, model_id: str) -> str | None:
            return None

    k8s = FirstDeployK8s()
    k8s.wait_behavior = ["timeout"]
    workflow = _workflow(k8s, state)

    with pytest.raises(DeployFailure):
        workflow.run(_make_request())

    assert k8s.patches == []


def test_transient_api_error_is_retried_and_succeeds():
    k8s = RecordingK8s()
    transient = ApiException(status=503, reason="Service Unavailable")
    k8s.upsert_errors = [transient]
    state = FakeStateStore()
    workflow = _workflow(k8s, state)

    workflow.run(_make_request())

    assert state.models["demo"]["tag"] == "1.0.0"


def test_permanent_api_error_does_not_retry():
    k8s = RecordingK8s()
    k8s.upsert_errors = [
        ApiException(status=400, reason="Bad Request"),
        ApiException(status=400, reason="Bad Request"),
        ApiException(status=400, reason="Bad Request"),
    ]
    state = FakeStateStore()
    workflow = _workflow(k8s, state)

    with pytest.raises(ApiException):
        workflow.run(_make_request())

    assert len(k8s.upsert_errors) == 2  # only one attempt consumed


def test_transient_failure_exhausts_attempts_raises_deploy_failure():
    k8s = RecordingK8s()
    k8s.upsert_errors = [ApiException(status=503) for _ in range(3)]
    state = FakeStateStore()
    workflow = _workflow(k8s, state, max_attempts=3)

    with pytest.raises(DeployFailure):
        workflow.run(_make_request())

    assert state.models == {}


def test_per_model_locks_do_not_block_other_models():
    k8s = RecordingK8s()
    state = FakeStateStore()
    lock_manager = ModelLockManager()
    workflow = _workflow(k8s, state, lock_manager=lock_manager)

    # Hold the lock for "other" and run a deploy for "demo" — it must not block.
    lock_manager.for_model("other").acquire()
    try:
        workflow.run(_make_request(model_id="demo"))
    finally:
        lock_manager.for_model("other").release()

    assert state.models["demo"]["tag"] == "1.0.0"


def test_same_model_lock_is_mutually_exclusive():
    lock_manager = ModelLockManager()
    lock_a = lock_manager.for_model("same")
    lock_b = lock_manager.for_model("same")
    assert lock_a is lock_b


def test_in_memory_registry_from_environment(monkeypatch):
    payload = [
        {
            "project_id": "demo",
            "image": "owner/app",
            "ghcr_username": "u",
            "ghcr_token": "t",
            "default_tag": "v1",
            "replicas": 2,
            "container_port": 9000,
            "service_port": 8080,
            "env": {"A": "1"},
        }
    ]
    monkeypatch.setenv("ML_SUPERVISOR_SEED_PROJECTS", json.dumps(payload))

    registry = InMemoryProjectRegistry.from_environment()
    project = registry.get("demo")

    assert project == Project(
        project_id="demo",
        image="owner/app",
        ghcr_username="u",
        ghcr_token="t",
        default_tag="v1",
        replicas=2,
        container_port=9000,
        service_port=8080,
        env={"A": "1"},
    )


def test_in_memory_registry_rejects_missing_fields(monkeypatch):
    monkeypatch.setenv(
        "ML_SUPERVISOR_SEED_PROJECTS",
        json.dumps([{"project_id": "demo", "image": "owner/app"}]),
    )
    with pytest.raises(ValueError):
        InMemoryProjectRegistry.from_environment()


def test_in_memory_registry_empty_env_returns_empty_registry(monkeypatch):
    monkeypatch.delenv("ML_SUPERVISOR_SEED_PROJECTS", raising=False)
    registry = InMemoryProjectRegistry.from_environment()
    assert registry.get("demo") is None


def test_upsert_model_renders_container_image_in_deployment_body():
    """Smoke test for #8: ensure the patched Deployment body carries the new image."""
    from app.services.kubernetes_service import KubernetesService

    service = KubernetesService(namespace="ns", image_pull_secret_name="ghcr-pull-secret")
    spec = DeploymentSpec(
        model_id="demo",
        image="ghcr.io/owner/app:2.0.0",
        replicas=1,
        container_port=8000,
        service_port=80,
        env={},
    )

    body = service._build_deployment_body("model-demo", spec)

    containers = body["spec"]["template"]["spec"]["containers"]
    assert containers[0]["image"] == "ghcr.io/owner/app:2.0.0"
    assert body["spec"]["template"]["spec"]["imagePullSecrets"] == [{"name": "ghcr-pull-secret"}]
