import pytest

from app.cluster.backend import BackendError, ModelSpec
from app.cluster.docker import DockerBackend, DockerInspector
from app.config import Settings
from app.registry.resources import Resources
from tests.fake_docker import FakeDockerClient, FakeImage

GI = 1024**3
RES = Resources(cpu_m=1500, memory_bytes=4 * GI, gpu=False, disk_bytes=10 * GI)
GPU = Resources(cpu_m=1500, memory_bytes=4 * GI, gpu=True, disk_bytes=10 * GI)
API_RES = Resources(cpu_m=100, memory_bytes=256 * 1024 * 1024, gpu=False, disk_bytes=0)
GHCR_IMAGE = "ghcr.io/org/ecg@sha256:" + "a" * 64


@pytest.fixture
def docker_client():
    return FakeDockerClient()


@pytest.fixture
def backend(docker_client):
    return DockerBackend(Settings(_env_file=None, docker_network="mlapi-models"), client=docker_client)


def spec(**kw):
    base = dict(name="ecg", image=GHCR_IMAGE, mode="sync", resources=RES, api_resources=API_RES)
    base.update(kw)
    return ModelSpec(**base)


async def test_sync_container_has_limits_labels_network_and_cache_volume(backend, docker_client):
    await backend.apply_model(spec(env={"HF_TOKEN": "t"}))
    c = docker_client.container_map["mlapi-m-ecg"]
    k = c.kwargs
    assert c.status == "running"
    assert k["nano_cpus"] == 1_500_000_000
    assert k["mem_limit"] == 4 * GI and k["memswap_limit"] == 4 * GI
    assert k["network"] == "mlapi-models"
    assert k["volumes"] == {"mlapi-m-ecg-cache": {"bind": "/cache", "mode": "rw"}}
    assert k["labels"] == {"mlapi.managed": "true", "mlapi.model": "ecg", "mlapi.role": "main"}
    assert k["environment"]["HF_TOKEN"] == "t"
    assert k["environment"]["FASTMLAPI_ROLE"] == "sync"
    assert k["environment"]["FASTMLAPI_CACHE_DIR"] == "/cache"
    assert k["environment"]["HF_HOME"] == "/cache/hf"
    assert "ports" not in k                                       # nothing is published to the host
    assert docker_client.network_map["mlapi-models"]["driver"] == "bridge"
    assert "mlapi-m-ecg-cache" in docker_client.volume_map


async def test_container_is_hardened(backend, docker_client):
    await backend.apply_model(spec())
    k = docker_client.container_map["mlapi-m-ecg"].kwargs
    assert k["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in k["security_opt"]
    assert k["restart_policy"] == {"Name": "unless-stopped"}
    assert k["pids_limit"] > 0
    assert not k.get("privileged")


async def test_platform_env_cannot_be_overridden_by_model_env(backend, docker_client):
    await backend.apply_model(spec(env={"FASTMLAPI_ROLE": "worker", "HF_HOME": "/evil"}))
    env = docker_client.container_map["mlapi-m-ecg"].kwargs["environment"]
    assert env["FASTMLAPI_ROLE"] == "sync" and env["HF_HOME"] == "/cache/hf"


async def test_gpu_request_only_when_approved(backend, docker_client):
    await backend.apply_model(spec(resources=GPU))
    requests = docker_client.container_map["mlapi-m-ecg"].kwargs["device_requests"]
    assert len(requests) == 1 and requests[0]["Count"] == 1
    await backend.apply_model(spec(resources=RES))
    assert not docker_client.container_map["mlapi-m-ecg"].kwargs.get("device_requests")


async def test_reapply_replaces_the_container(backend, docker_client):
    await backend.apply_model(spec())
    await backend.apply_model(spec(image="ghcr.io/org/ecg@sha256:" + "b" * 64))
    assert len(docker_client.container_map) == 1
    assert docker_client.container_map["mlapi-m-ecg"].image.endswith("b" * 64)


async def test_registry_images_are_pulled_only_when_missing_and_local_ids_never(backend, docker_client):
    await backend.apply_model(spec())
    assert docker_client.pulled == [GHCR_IMAGE]
    docker_client.pulled.clear()
    await backend.apply_model(spec())                              # now present locally
    assert docker_client.pulled == []
    docker_client.local_images["sha256:" + "c" * 64] = FakeImage("local", {}, "sha256:" + "c" * 64)
    await backend.apply_model(spec(image="sha256:" + "c" * 64))
    assert docker_client.pulled == []


async def test_pull_failure_is_a_backend_error(backend, docker_client):
    docker_client.unpullable.add(GHCR_IMAGE)
    with pytest.raises(BackendError):
        await backend.apply_model(spec())
    assert docker_client.container_map == {}


async def test_missing_local_image_is_a_backend_error(backend):
    with pytest.raises(BackendError):
        await backend.apply_model(spec(image="sha256:" + "d" * 64))


async def test_queue_mode_creates_api_and_stopped_worker(backend, docker_client):
    await backend.apply_model(spec(mode="queue", resources=GPU, queue_url="redis://m_ecg:pw@redis:6379/0", max_job_seconds=900))
    api, worker = docker_client.container_map["mlapi-m-ecg-api"], docker_client.container_map["mlapi-m-ecg-worker"]
    assert api.status == "running" and worker.status == "created"
    assert api.kwargs["environment"]["FASTMLAPI_ROLE"] == "api"
    assert worker.kwargs["environment"]["FASTMLAPI_ROLE"] == "worker"
    for c in (api, worker):
        assert c.kwargs["environment"]["FASTMLAPI_QUEUE_URL"] == "redis://m_ecg:pw@redis:6379/0"
    assert api.kwargs["nano_cpus"] == 100_000_000 and not api.kwargs.get("device_requests")
    assert "volumes" not in api.kwargs or not api.kwargs["volumes"]
    assert worker.kwargs["nano_cpus"] == 1_500_000_000 and worker.kwargs["device_requests"]
    assert worker.kwargs["stop_timeout"] == 900
    assert await backend.endpoint("ecg") == "http://mlapi-m-ecg-api:8000"


async def test_reapply_keeps_a_running_worker_running(backend, docker_client):
    await backend.apply_model(spec(mode="queue"))
    await backend.scale("ecg", 1, role="worker")
    await backend.apply_model(spec(mode="queue", image="ghcr.io/org/ecg@sha256:" + "b" * 64))
    assert docker_client.container_map["mlapi-m-ecg-worker"].status == "running"
    assert docker_client.container_map["mlapi-m-ecg-worker"].image.endswith("b" * 64)


async def test_scale_start_stop_and_unknown(backend, docker_client):
    await backend.apply_model(spec())
    await backend.scale("ecg", 0)
    assert docker_client.container_map["mlapi-m-ecg"].status == "exited"
    await backend.scale("ecg", 1)
    assert docker_client.container_map["mlapi-m-ecg"].status == "running"
    with pytest.raises(BackendError):
        await backend.scale("nope", 1)
    with pytest.raises(BackendError):
        await backend.scale("ecg", 1, role="worker")               # sync models have no worker


async def test_status_reports_running_restarts_and_queue_roles(backend, docker_client):
    assert (await backend.status("ecg")).exists is False
    await backend.apply_model(spec())
    s = await backend.status("ecg")
    assert (s.exists, s.ready, s.replicas, s.restarts) == (True, True, 1, 0)
    docker_client.container_map["mlapi-m-ecg"].restart_count = 5
    docker_client.container_map["mlapi-m-ecg"].restarting = True
    s = await backend.status("ecg")
    assert s.ready is False and s.restarts == 5
    await backend.scale("ecg", 0)
    s = await backend.status("ecg")
    assert s.replicas == 0 and s.ready is False


async def test_queue_status_counts_worker_separately(backend, docker_client):
    await backend.apply_model(spec(mode="queue"))
    s = await backend.status("ecg")
    assert s.ready and s.replicas == 1 and s.worker_replicas == 0
    await backend.scale("ecg", 1, role="worker")
    assert (await backend.status("ecg")).worker_replicas == 1


async def test_delete_removes_containers_and_optionally_the_cache(backend, docker_client):
    await backend.apply_model(spec(mode="queue"))
    await backend.delete_model("ecg", keep_cache=True)
    assert docker_client.container_map == {} and "mlapi-m-ecg-cache" in docker_client.volume_map
    await backend.apply_model(spec())
    await backend.delete_model("ecg", keep_cache=False)
    assert "mlapi-m-ecg-cache" not in docker_client.volume_map
    await backend.delete_model("ecg")                              # deleting twice is fine


async def test_list_models_uses_labels(backend, docker_client):
    await backend.apply_model(spec(name="a"))
    await backend.apply_model(spec(name="b", mode="queue"))
    from tests.fake_docker import FakeContainer
    docker_client.container_map["other"] = FakeContainer(docker_client, "other", "x", {"labels": {}})
    assert await backend.list_models() == ["a", "b"]


async def test_secret_refs_are_ignored_with_a_warning(backend, docker_client, caplog):
    with caplog.at_level("WARNING"):
        await backend.apply_model(spec(secret_refs=("hf-token",)))
    assert "secret" in caplog.text.lower()
    assert "hf-token" not in str(docker_client.container_map["mlapi-m-ecg"].kwargs["environment"])


async def test_endpoints(backend):
    assert await backend.endpoint("ecg") == "http://mlapi-m-ecg:8000"


# ───────────── local image inspection ─────────────
async def test_inspector_reads_local_image_labels_and_id(docker_client):
    docker_client.local_images["ecg-local:latest"] = FakeImage("ecg-local:latest", {"mlapi.model": "true"}, "sha256:" + "e" * 64)
    info = await DockerInspector(docker_client).inspect("ecg-local:latest", "local")
    assert info.labels == {"mlapi.model": "true"} and info.digest == "sha256:" + "e" * 64
    assert info.package_name == "ecg-local"


async def test_inspector_unknown_image_and_wrong_source(docker_client):
    with pytest.raises(LookupError):
        await DockerInspector(docker_client).inspect("nope:1", "local")
    with pytest.raises(LookupError):
        await DockerInspector(docker_client).inspect("ghcr.io/org/x:latest", "ghcr")


async def test_ports_are_published_on_localhost_only_when_enabled(docker_client):
    backend = DockerBackend(Settings(_env_file=None, docker_publish_ports=True), client=docker_client)
    await backend.apply_model(spec())
    assert docker_client.container_map["mlapi-m-ecg"].kwargs["ports"] == {"8000/tcp": ("127.0.0.1", None)}
    assert await backend.endpoint("ecg") == "http://127.0.0.1:32768"


async def test_queue_publishes_only_the_api_port(docker_client):
    backend = DockerBackend(Settings(_env_file=None, docker_publish_ports=True), client=docker_client)
    await backend.apply_model(spec(mode="queue"))
    assert "ports" in docker_client.container_map["mlapi-m-ecg-api"].kwargs
    assert "ports" not in docker_client.container_map["mlapi-m-ecg-worker"].kwargs


# ───────────── review findings ─────────────
async def test_workloads_without_a_gpu_cannot_see_the_hosts_gpus(backend, docker_client):
    """CUDA base images set NVIDIA_VISIBLE_DEVICES=all, which would hand every GPU to a container that was never approved one."""
    await backend.apply_model(spec(mode="queue"))
    for name in ("mlapi-m-ecg-api", "mlapi-m-ecg-worker"):
        assert docker_client.container_map[name].kwargs["environment"]["NVIDIA_VISIBLE_DEVICES"] == "void"
    await backend.apply_model(spec(name="plain"))
    assert docker_client.container_map["mlapi-m-plain"].kwargs["environment"]["NVIDIA_VISIBLE_DEVICES"] == "void"


async def test_an_approved_gpu_workload_is_not_blinded(backend, docker_client):
    await backend.apply_model(spec(resources=GPU, env={"NVIDIA_VISIBLE_DEVICES": "all"}))
    env = docker_client.container_map["mlapi-m-ecg"].kwargs["environment"]
    assert "NVIDIA_VISIBLE_DEVICES" not in env          # the GPU comes from the device request, not from an env override


async def test_a_running_worker_is_stopped_gracefully_before_it_is_replaced(backend, docker_client):
    await backend.apply_model(spec(mode="queue", max_job_seconds=900))
    await backend.scale("ecg", 1, role="worker")
    await backend.apply_model(spec(mode="queue", max_job_seconds=900, image="ghcr.io/org/ecg@sha256:" + "b" * 64))
    assert ("mlapi-m-ecg-worker", 900) in docker_client.stop_calls      # it gets time to finish its current job


async def test_the_unenforceable_disk_limit_is_logged(backend, caplog):
    with caplog.at_level("WARNING"):
        await backend.apply_model(spec())
    assert "disk" in caplog.text.lower() and "not enforced" in caplog.text.lower()
