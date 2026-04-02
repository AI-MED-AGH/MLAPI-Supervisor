from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app, get_kubernetes_service, get_state_store
from app.services.kubernetes_service import DeploymentSpec


class FakeKubernetesService:
    def __init__(self) -> None:
        self.deployments: dict[str, dict[str, Any]] = {}
        self.pull_secret_calls: list[tuple[str, str, str]] = []

    def ensure_registry_pull_secret(self, *, username: str, token: str, registry: str) -> None:
        self.pull_secret_calls.append((username, token, registry))

    def upsert_model(self, spec: DeploymentSpec) -> dict[str, Any]:
        deployment_name = f"model-{spec.model_id}"
        service_name = f"model-{spec.model_id}-svc"
        self.deployments[spec.model_id] = {
            "image": spec.image,
            "deployment_name": deployment_name,
            "service_name": service_name,
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


class FakeStateStore:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, str]] = {}

    def set_model(self, *, model_id: str, image: str, tag: str) -> None:
        self.models[model_id] = {"image": image, "tag": tag}

    def get_model(self, model_id: str) -> dict[str, str] | None:
        return self.models.get(model_id)

    def get_tag(self, model_id: str) -> str | None:
        model = self.models.get(model_id)
        if model is None:
            return None
        return model.get("tag")

    def delete_model(self, model_id: str) -> None:
        self.models.pop(model_id, None)

    def list_models(self) -> dict[str, dict[str, str]]:
        return dict(self.models)


@pytest.fixture()
def test_client():
    fake_kubernetes_service = FakeKubernetesService()
    fake_state_store = FakeStateStore()
    settings = Settings(
        k8s_namespace="ml-models",
        k8s_service_type="LoadBalancer",
        ghcr_registry="ghcr.io",
        ghcr_username="ai-med-agh",
        ghcr_token="token-123",
        enable_polling=False,
    )

    app.dependency_overrides[get_kubernetes_service] = lambda: fake_kubernetes_service
    app.dependency_overrides[get_state_store] = lambda: fake_state_store
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        yield client, fake_kubernetes_service, fake_state_store

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
    client, fake_kubernetes_service, _ = test_client

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
    assert fake_kubernetes_service.pull_secret_calls


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
    status_payload = status_response.json()
    assert status_payload["phase"] == "ready"
    assert status_payload["image"] == "ghcr.io/ai-med-agh/ct-segmentation-api:2.0.0"

    list_response = client.get("/models")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload["items"]) == 1
    assert list_payload["items"][0]["model_id"] == "ct-segmentation"


def test_delete_model(test_client):
    client, _, fake_state_store = test_client

    deploy_response = client.post(
        "/models/deploy",
        json={
            "model_id": "xray-detector",
            "image": "ai-med-agh/xray-detector",
            "tag": "0.9.0",
            "replicas": 1,
        },
    )
    assert deploy_response.status_code == 201

    delete_response = client.delete("/models/xray-detector")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"model_id": "xray-detector", "deleted": True}
    assert fake_state_store.get_model("xray-detector") is None


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
