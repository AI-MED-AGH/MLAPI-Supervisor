from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from kubernetes import client

from app.main import app, get_kubernetes_service
from app.services import KubernetesService

mock_client = TestClient(app)


def test_get_models_status_endpoint():
    """Test data filtering and enpoint connection"""
    mock_kubernetes_service = MagicMock()

    mock_kubernetes_service.get_models_cluster_status.return_value = [
        {
            "name": "yolo-v8",
            "status": "Running",
            "replicas": {
                "ready": 1,
                "available": 1,
                "desired": 2,
            },
            "cpu_usage": "2100m",
            "pods": [
                {
                    "name": "yolo-v8-pod-1",
                    "phase": "Running",
                    "state": "running",
                    "reason": None,
                    "cpu_usage": "1050m",
                },
                {
                    "name": "yolo-v8-pod-2",
                    "phase": "Running",
                    "state": "running",
                    "reason": None,
                    "cpu_usage": "1050m",
                },
            ],
        },
        {
            "name": "yolo-v9",
            "status": "Running",
            "replicas": {
                "ready": 4,
                "available": 3,
                "desired": 4,
            },
            "cpu_usage": "2100m",
            "pods": [
                {
                    "name": "yolo-v9-pod-1",
                    "phase": "Running",
                    "state": "running",
                    "reason": None,
                    "cpu_usage": "1050m",
                },
                {
                    "name": "yolo-v9-pod-2",
                    "phase": "Running",
                    "state": "running",
                    "reason": None,
                    "cpu_usage": "1050m",
                },
            ],
        },
    ]
    app.dependency_overrides[get_kubernetes_service] = lambda: mock_kubernetes_service

    try:
        response = mock_client.get("/status/models")
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, list)
        assert len(data) == 2

        model = data[0]
        assert model["name"] == "yolo-v8"
        assert model["status"] == "Running"
        assert model["replicas"] == 1
        assert model["cpu_usage"] == "2100m"

        assert len(model.items()) == 4

        model_2 = data[1]
        assert len(model_2.items()) == 4
        assert model_2["replicas"] == 4

        mock_kubernetes_service.get_models_cluster_status.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def create_fake_deployment_list():
    dummy_template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(),
        spec=client.V1PodSpec(containers=[client.V1Container(name="dummy-container")]),
    )
    deployments = [
        client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name="yolo-v8-dep",
                labels={"model-id": "yolo-v8", "managed-by": "mlapi-supervisor"},
            ),
            spec=client.V1DeploymentSpec(
                replicas=2, selector=client.V1LabelSelector(), template=dummy_template
            ),
            status=client.V1DeploymentStatus(ready_replicas=2, available_replicas=2),
        ),
        client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name="resnet50-dep",
                labels={"model-id": "resnet50", "managed-by": "mlapi-supervisor"},
            ),
            spec=client.V1DeploymentSpec(
                replicas=2, selector=client.V1LabelSelector(), template=dummy_template
            ),
            status=client.V1DeploymentStatus(ready_replicas=1, available_replicas=1),
        ),
        client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name="llama3-dep",
                labels={"model-id": "llama3", "managed-by": "mlapi-supervisor"},
            ),
            spec=client.V1DeploymentSpec(
                replicas=3, selector=client.V1LabelSelector(), template=dummy_template
            ),
            status=client.V1DeploymentStatus(ready_replicas=2, available_replicas=1),
        ),
        client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name="empty",
                labels={"model-id": "empty", "managed-by": "mlapi-supervisor"},
            ),
            spec=client.V1DeploymentSpec(
                replicas=0, selector=client.V1LabelSelector(), template=dummy_template
            ),
            status=client.V1DeploymentStatus(ready_replicas=0, available_replicas=0),
        ),
    ]
    return client.V1DeploymentList(items=deployments)


def create_fake_pod_list():
    def make_container_status(state_type, reason=None):
        state = client.V1ContainerState()
        if state_type == "running":
            state.running = client.V1ContainerStateRunning()
        elif state_type == "waiting":
            state.waiting = client.V1ContainerStateWaiting(reason=reason)

        return [
            client.V1ContainerStatus(
                name="model",
                image="...",
                image_id="docker-pullable://...",
                restart_count=0,
                ready=(state_type == "running"),
                state=state,
            )
        ]

    pods = [
        client.V1Pod(
            metadata=client.V1ObjectMeta(
                name="yolo-v8-pod-1", labels={"model-id": "yolo-v8"}
            ),
            status=client.V1PodStatus(
                phase="Running", container_statuses=make_container_status("running")
            ),
        ),
        client.V1Pod(
            metadata=client.V1ObjectMeta(
                name="yolo-v8-pod-2", labels={"model-id": "yolo-v8"}
            ),
            status=client.V1PodStatus(
                phase="Running", container_statuses=make_container_status("running")
            ),
        ),
        client.V1Pod(
            metadata=client.V1ObjectMeta(
                name="resnet50-pod-1", labels={"model-id": "resnet50"}
            ),
            status=client.V1PodStatus(
                phase="Running", container_statuses=make_container_status("running")
            ),
        ),
        client.V1Pod(
            metadata=client.V1ObjectMeta(
                name="resnet50-pod-err", labels={"model-id": "resnet50"}
            ),
            status=client.V1PodStatus(
                phase="Running",
                container_statuses=make_container_status("waiting", "CrashLoopBackOff"),
            ),
        ),
        client.V1Pod(
            metadata=client.V1ObjectMeta(
                name="llama3-pod-1", labels={"model-id": "llama3"}
            ),
            status=client.V1PodStatus(
                phase="Running", container_statuses=make_container_status("running")
            ),
        ),
        client.V1Pod(
            metadata=client.V1ObjectMeta(
                name="llama3-pod-2", labels={"model-id": "llama3"}
            ),
            status=client.V1PodStatus(
                phase="Running", container_statuses=make_container_status("running")
            ),
        ),
    ]
    return client.V1PodList(items=pods)


def create_fake_metrics():
    return {
        "items": [
            {
                "metadata": {"name": "yolo-v8-pod-1"},
                "containers": [{"usage": {"cpu": "1000000000n"}}],
            },  # 1000m
            {
                "metadata": {"name": "yolo-v8-pod-2"},
                "containers": [{"usage": {"cpu": "1100000000n"}}],
            },  # 1100m
            {
                "metadata": {"name": "resnet50-pod-1"},
                "containers": [{"usage": {"cpu": "500000000n"}}],
            },  # 500m
            {
                "metadata": {"name": "llama3-pod-1"},
                "containers": [{"usage": {"cpu": "2000000000n"}}],
            },  # 2000m
            {
                "metadata": {"name": "llama3-pod-2"},
                "containers": [{"usage": {"cpu": "2000000000n"}}],
            },  # 2000m
        ]
    }


@patch("kubernetes.config.load_incluster_config")
@patch("kubernetes.client.CustomObjectsApi.list_namespaced_custom_object")
@patch("kubernetes.client.CoreV1Api.list_namespaced_pod")
@patch("kubernetes.client.AppsV1Api.list_namespaced_deployment")
def test_get_models_cluster_snapshot(
    mock_deploy, mock_pods, mock_metrics, mock_load_config
):
    mock_deploy.return_value = create_fake_deployment_list()
    mock_pods.return_value = create_fake_pod_list()
    mock_metrics.return_value = create_fake_metrics()

    service = KubernetesService(namespace="ml-production")
    results = service.get_models_cluster_status()

    models = {m.name: m for m in results}
    assert len(models) == 4

    yolo = models["yolo-v8"]
    assert yolo.status == "Running"
    assert yolo.replicas.desired == 2
    assert yolo.replicas.ready == 2
    assert yolo.replicas.available == 2
    assert yolo.cpu_usage == "2100m"  # 1000m + 1100m
    assert len(yolo.pods) == 2
    assert all(p.state == "running" for p in yolo.pods)
    assert all(p.reason is None for p in yolo.pods)

    resnet = models["resnet50"]
    assert resnet.status == "CrashLoopBackOff"
    assert resnet.replicas.desired == 2
    assert resnet.replicas.ready == 1
    assert resnet.replicas.available == 1
    assert resnet.cpu_usage == "500m"
    assert len(resnet.pods) == 2

    err_pod = next(p for p in resnet.pods if p.name == "resnet50-pod-err")
    assert err_pod.phase == "Running"
    assert err_pod.state == "waiting"
    assert err_pod.reason == "CrashLoopBackOff"
    assert err_pod.cpu_usage == "0m"

    llama = models["llama3"]
    assert llama.status == "Degraded"
    assert llama.replicas.desired == 3
    assert llama.replicas.ready == 2
    assert llama.replicas.available == 1
    assert llama.cpu_usage == "4000m"
    assert len(llama.pods) == 2
    assert all(p.state == "running" for p in llama.pods)

    empty = models["empty"]
    assert empty.status == "ScaledToZero"
    assert empty.replicas.desired == 0
    assert empty.replicas.ready == 0
    assert empty.replicas.available == 0
    assert empty.cpu_usage == "0m"
    assert len(empty.pods) == 0
