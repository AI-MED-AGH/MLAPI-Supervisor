import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import instance_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_instances():
    instance_service.clear_all_instances()
    yield
    instance_service.clear_all_instances()


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_instances_empty():
    response = client.get("/instances/")
    assert response.status_code == 200
    assert response.json() == []


def test_create_instance():
    payload = {"name": "test-model", "model_name": "bert-base"}
    response = client.post("/instances/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "test-model"
    assert data["model_name"] == "bert-base"
    assert data["status"] == "running"
    assert "id" in data


def test_get_instance():
    payload = {"name": "test-model", "model_name": "gpt2"}
    create_response = client.post("/instances/", json=payload)
    instance_id = create_response.json()["id"]

    response = client.get(f"/instances/{instance_id}")
    assert response.status_code == 200
    assert response.json()["id"] == instance_id


def test_get_instance_not_found():
    response = client.get("/instances/nonexistent-id")
    assert response.status_code == 404


def test_delete_instance():
    payload = {"name": "to-delete", "model_name": "resnet50"}
    create_response = client.post("/instances/", json=payload)
    instance_id = create_response.json()["id"]

    delete_response = client.delete(f"/instances/{instance_id}")
    assert delete_response.status_code == 204

    get_response = client.get(f"/instances/{instance_id}")
    assert get_response.status_code == 404


def test_delete_instance_not_found():
    response = client.delete("/instances/nonexistent-id")
    assert response.status_code == 404
