from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import (
    app,
    get_deploy_workflow,
    get_ghcr_service,
    get_kubernetes_service,
    get_project_registry,
    get_state_store,
)
from app.services import (
    DeployWorkflow,
    InMemoryProjectRegistry,
    ModelLockManager,
    Project,
)
from app.services.kubernetes_service import DeploymentSpec


class FakeKubernetesService:
    def __init__(self) -> None:
        self.deployments: dict[str, dict[str, Any]] = {}
        self.pull_secret_calls: list[tuple[str, str, str]] = []

    def ensure_registry_pull_secret(self, *, username: str, token: str, registry: str) -> None:
        self.pull_secret_calls.append((username, token, registry))

    def upsert_model(self, spec: DeploymentSpec) -> dict[str, Any]:
        self.deployments[spec.model_id] = {
            "image": spec.image,
            "deployment_name": f"model-{spec.model_id}",
            "service_name": f"model-{spec.model_id}-svc",
            "desired_replicas": spec.replicas,
            "ready_replicas": spec.replicas,
            "available_replicas": spec.replicas,
            "service_type": "LoadBalancer",
            "external_endpoints": ["192.168.10.10"],
        }
        return self.get_model_status(spec.model_id)

    def delete_model(self, model_id: str) -> bool:
        return self.deployments.pop(model_id, None) is not None

    def get_model_status(self, model_id: str) -> dict[str, Any]:
        deployment = self.deployments.get(model_id)
        if deployment is None:
            raise KeyError(f"Model deployment '{model_id}' was not found")

        return {
            "model_id": model_id,
            "deployment_name": deployment["deployment_name"],
            "service_name": deployment["service_name"],
            "namespace": "ml-models",
            "phase": "ready",
            "desired_replicas": deployment["desired_replicas"],
            "ready_replicas": deployment["ready_replicas"],
            "available_replicas": deployment["available_replicas"],
            "service_type": deployment["service_type"],
            "external_endpoints": deployment["external_endpoints"],
        }

    def list_models(self) -> list[dict[str, Any]]:
        return [self.get_model_status(model_id) for model_id in sorted(self.deployments.keys())]

    def get_deployment_image(self, model_id: str) -> str | None:
        deployment = self.deployments.get(model_id)
        return deployment["image"] if deployment else None

    def wait_for_rollout(self, model_id: str, *, timeout_seconds: float, poll_interval: float = 2.0) -> None:
        if model_id not in self.deployments:
            raise TimeoutError(f"no deployment '{model_id}'")

    def patch_deployment_image(self, model_id: str, image: str) -> None:
        deployment = self.deployments.get(model_id)
        if deployment is None:
            raise KeyError(model_id)
        deployment["image"] = image


class FakeStateStore:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, str]] = {}

    def set_model(self, *, model_id: str, image: str, tag: str) -> None:
        self.models[model_id] = {"image": image, "tag": tag}

    def get_model(self, model_id: str) -> dict[str, str] | None:
        return self.models.get(model_id)

    def get_tag(self, model_id: str) -> str | None:
        model = self.models.get(model_id)
        return None if model is None else model.get("tag")

    def delete_model(self, model_id: str) -> None:
        self.models.pop(model_id, None)

    def list_models(self) -> dict[str, dict[str, str]]:
        return dict(self.models)


class FakeGHCRService:
    def __init__(self, tag_map: dict[str, str] | None = None) -> None:
        self._tag_map = tag_map or {}

    def get_latest_tag(self, image: str) -> str | None:
        return self._tag_map.get(image)

    def list_tags(self, image: str) -> list[str]:
        tag = self._tag_map.get(image)
        return [tag] if tag else []


@pytest.fixture()
def test_client():
    fake_k8s = FakeKubernetesService()
    fake_state = FakeStateStore()
    fake_ghcr = FakeGHCRService(tag_map={"ai-med-agh/demo-api": "2.3.0"})
    project_registry = InMemoryProjectRegistry(
        [
            Project(
                project_id="demo",
                image="ai-med-agh/demo-api",
                ghcr_username="ai-med-agh",
                ghcr_token="project-token",
                default_tag="latest",
                replicas=1,
                container_port=8080,
                service_port=80,
                env={"STAGE": "prod"},
            )
        ]
    )
    lock_manager = ModelLockManager()
    settings = Settings(
        k8s_namespace="ml-models",
        k8s_service_type="LoadBalancer",
        ghcr_registry="ghcr.io",
        ghcr_username="ai-med-agh",
        ghcr_token="token-123",
        enable_polling=False,
    )
    workflow = DeployWorkflow(
        kubernetes_service=fake_k8s,
        state_store=fake_state,
        lock_manager=lock_manager,
        registry=settings.ghcr_registry,
        rollout_timeout_seconds=5.0,
        rollout_poll_interval=0.01,
        max_attempts=1,
        sleep_fn=lambda _s: None,
    )

    app.dependency_overrides[get_kubernetes_service] = lambda: fake_k8s
    app.dependency_overrides[get_state_store] = lambda: fake_state
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_deploy_workflow] = lambda: workflow
    app.dependency_overrides[get_project_registry] = lambda: project_registry
    app.dependency_overrides[get_ghcr_service] = lambda: fake_ghcr

    with TestClient(app) as client:
        yield client, fake_k8s, fake_state

    app.dependency_overrides.clear()


def test_root(test_client):
    client, _, _ = test_client
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to MLAPI Supervisor"}


def test_health_check(test_client):
    client, _, _ = test_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_deploy_model(test_client):
    client, fake_k8s, fake_state = test_client

    response = client.post(
        "/models/deploy",
        json={
            "model_id": "heart-risk-model",
            "image": "ai-med-agh/heart-risk-api",
            "tag": "1.2.3",
            "replicas": 2,
            "container_port": 9000,
            "service_port": 80,
            "env": {"MODEL_VARIANT": "production"},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["model_id"] == "heart-risk-model"
    assert payload["image"] == "ghcr.io/ai-med-agh/heart-risk-api:1.2.3"
    assert payload["service_type"] == "LoadBalancer"
    assert payload["external_endpoints"] == ["192.168.10.10"]
    assert fake_k8s.pull_secret_calls
    assert fake_state.get_tag("heart-risk-model") == "1.2.3"


def test_model_status_and_listing(test_client):
    client, _, _ = test_client

    deploy_response = client.post(
        "/models/deploy",
        json={
            "model_id": "ct-segmentation",
            "image": "ai-med-agh/ct-segmentation-api",
            "tag": "2.0.0",
            "replicas": 1,
        },
    )
    assert deploy_response.status_code == 201

    status_response = client.get("/models/ct-segmentation/status")
    assert status_response.status_code == 200
    assert status_response.json()["phase"] == "ready"
    assert status_response.json()["image"] == "ghcr.io/ai-med-agh/ct-segmentation-api:2.0.0"

    list_response = client.get("/models")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["model_id"] == "ct-segmentation"


def test_delete_model(test_client):
    client, _, fake_state = test_client

    assert client.post(
        "/models/deploy",
        json={
            "model_id": "xray-detector",
            "image": "ai-med-agh/xray-detector",
            "tag": "0.9.0",
            "replicas": 1,
        },
    ).status_code == 201

    delete_response = client.delete("/models/xray-detector")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"model_id": "xray-detector", "deleted": True}
    assert fake_state.get_model("xray-detector") is None


def test_invalid_model_id_returns_422(test_client):
    client, _, _ = test_client
    response = client.post(
        "/models/deploy",
        json={
            "model_id": "Bad Model",
            "image": "ai-med-agh/heart-risk-api",
            "tag": "1.0.0",
            "replicas": 1,
        },
    )
    assert response.status_code == 422


def test_pull_project_uses_registry_credentials_and_latest_tag(test_client):
    client, fake_k8s, fake_state = test_client

    response = client.post("/projects/demo/pull")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_id"] == "demo"
    assert payload["image"] == "ghcr.io/ai-med-agh/demo-api:2.3.0"
    assert ("ai-med-agh", "project-token", "ghcr.io") in fake_k8s.pull_secret_calls
    assert fake_state.get_tag("demo") == "2.3.0"


def test_pull_project_accepts_tag_override(test_client):
    client, _, fake_state = test_client

    response = client.post("/projects/demo/pull", json={"tag": "1.0.0"})

    assert response.status_code == 200, response.text
    assert response.json()["image"] == "ghcr.io/ai-med-agh/demo-api:1.0.0"
    assert fake_state.get_tag("demo") == "1.0.0"


def test_pull_project_404_when_unknown(test_client):
    client, _, _ = test_client
    response = client.post("/projects/unknown/pull")
    assert response.status_code == 404
