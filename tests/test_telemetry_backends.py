from types import SimpleNamespace as NS

import pytest

from app.cluster.backend import ModelSpec, Usage
from app.cluster.docker import DockerBackend
from app.cluster.kubernetes import K8sApi, KubernetesBackend, _CPU_UNITS, _MEMORY_UNITS, _quantity
from app.config import Settings
from app.registry.resources import Resources
from tests.fake_docker import FakeDockerClient, FakeImage
from tests.fake_k8s import ApiException, FakeApps, FakeCore, FakeNet

GI = 1024**3
RES = Resources(cpu_m=1000, memory_bytes=2 * GI, gpu=False, disk_bytes=5 * GI)


def docker_stats(cpu_delta, system_delta, cpus, usage, inactive, v1=False):
    counters = {"total_inactive_file": inactive} if v1 else {"inactive_file": inactive}
    return {
        "precpu_stats": {"cpu_usage": {"total_usage": 1_000}, "system_cpu_usage": 10_000},
        "cpu_stats": {"cpu_usage": {"total_usage": 1_000 + cpu_delta}, "system_cpu_usage": 10_000 + system_delta,
                      "online_cpus": cpus},
        "memory_stats": {"usage": usage, "stats": counters},
    }


@pytest.fixture
def client():
    c = FakeDockerClient()
    c.local_images["img"] = FakeImage("img", {}, "sha256:img")
    return c


@pytest.fixture
def docker_backend(client):
    return DockerBackend(Settings(_env_file=None), client=client)


async def test_docker_usage_is_none_when_nothing_runs(docker_backend):
    assert await docker_backend.usage("ecg") is None


async def test_docker_usage_matches_how_docker_stats_computes_it(docker_backend, client):
    await docker_backend.apply_model(ModelSpec(name="ecg", image="img", mode="sync", resources=RES))
    # 25% of 4 cpus' time on the host = one full core = 1000 millicores; page cache is not counted as memory
    client.container_map["mlapi-m-ecg"].stats_result = docker_stats(2_500, 10_000, 4, usage=3 * GI, inactive=GI)
    assert await docker_backend.usage("ecg") == Usage(cpu_m=1000.0, memory_bytes=2 * GI)


async def test_docker_usage_supports_cgroup_v1_counters_and_idle_containers(docker_backend, client):
    await docker_backend.apply_model(ModelSpec(name="ecg", image="img", mode="sync", resources=RES))
    client.container_map["mlapi-m-ecg"].stats_result = docker_stats(0, 0, 2, usage=GI, inactive=GI // 2, v1=True)
    assert await docker_backend.usage("ecg") == Usage(cpu_m=0.0, memory_bytes=GI // 2)


async def test_docker_usage_of_a_queue_model_adds_api_and_worker(docker_backend, client):
    spec = ModelSpec(name="q", image="img", mode="queue", resources=RES, api_resources=RES)
    await docker_backend.apply_model(spec)
    await docker_backend.scale("q", 1, "worker")
    client.container_map["mlapi-m-q-api"].stats_result = docker_stats(500, 10_000, 2, usage=GI, inactive=0)
    client.container_map["mlapi-m-q-worker"].stats_result = docker_stats(1_000, 10_000, 2, usage=2 * GI, inactive=0)
    assert await docker_backend.usage("q") == Usage(cpu_m=300.0, memory_bytes=3 * GI)


async def test_docker_usage_ignores_the_all_zero_record_of_a_container_that_just_stopped(docker_backend, client):
    await docker_backend.apply_model(ModelSpec(name="ecg", image="img", mode="sync", resources=RES))
    client.container_map["mlapi-m-ecg"].stats_result = {
        "cpu_stats": {"cpu_usage": {"total_usage": 0}}, "precpu_stats": {"cpu_usage": {"total_usage": 0}}, "memory_stats": {}}
    assert await docker_backend.usage("ecg") is None


async def test_docker_usage_ignores_a_container_that_vanishes_mid_read(docker_backend, client):
    await docker_backend.apply_model(ModelSpec(name="ecg", image="img", mode="sync", resources=RES))
    client.container_map["mlapi-m-ecg"].stats_result = None
    assert await docker_backend.usage("ecg") is None


# ───────────── kubernetes ─────────────

class FakeCustom:
    def __init__(self, items=None, error=None):
        self.items, self.error, self.calls = items or [], error, []

    async def list_namespaced_custom_object(self, group, version, ns, plural, label_selector=None):
        self.calls.append((group, version, ns, plural, label_selector))
        if self.error:
            raise self.error
        return {"items": self.items}


def k8s(custom):
    return KubernetesBackend(Settings(_env_file=None), api=K8sApi(apps=FakeApps(), core=FakeCore(), net=FakeNet(), custom=custom))


def pod(*usages):
    return {"containers": [{"name": "m", "usage": {"cpu": c, "memory": m}} for c, m in usages]}


@pytest.mark.parametrize("raw,millicores", [("250m", 250), ("1", 1000), ("2500000n", 2.5), ("1500u", 1.5), ("0", 0)])
def test_cpu_quantities(raw, millicores):
    assert _quantity(raw, _CPU_UNITS) == pytest.approx(millicores)


@pytest.mark.parametrize("raw,bytes_", [("128974848", 128974848), ("1024Ki", 1024**2), ("2Gi", 2 * GI), ("5M", 5_000_000)])
def test_memory_quantities(raw, bytes_):
    assert _quantity(raw, _MEMORY_UNITS) == bytes_


async def test_k8s_usage_sums_all_pods_of_the_model():
    custom = FakeCustom([pod(("100m", "1Gi")), pod(("400000000n", "512Mi"))])
    assert await k8s(custom).usage("ecg") == Usage(cpu_m=500.0, memory_bytes=GI + 512 * 1024**2)
    assert custom.calls == [("metrics.k8s.io", "v1beta1", "mlapi-models", "pods", "mlapi/model=ecg")]


async def test_k8s_usage_is_none_without_pods_or_metrics_server():
    assert await k8s(FakeCustom([])).usage("ecg") is None
    assert await k8s(FakeCustom(error=ApiException(404))).usage("ecg") is None
    assert await k8s(None).usage("ecg") is None
