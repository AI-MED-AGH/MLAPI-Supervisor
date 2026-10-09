import pytest
import yaml

from app.cluster import k8s_manifests as m
from app.cluster.backend import BackendError, ModelSpec
from app.cluster.kubernetes import K8sApi, KubernetesBackend
from app.config import Settings
from app.registry.resources import Resources
from tests.fake_k8s import FakeApps, FakeCore, FakeNet, pod

GI = 1024**3
RES = Resources(cpu_m=1500, memory_bytes=4 * GI, gpu=False, disk_bytes=10 * GI)
GPU = Resources(cpu_m=2000, memory_bytes=8 * GI, gpu=True, disk_bytes=20 * GI)
API_RES = Resources(cpu_m=100, memory_bytes=256 * 1024 * 1024, gpu=False, disk_bytes=0)
IMAGE = "ghcr.io/org/ecg@sha256:" + "a" * 64
S = Settings(_env_file=None, k8s_namespace="mlapi-models", storage_class="fast")


def spec(**kw):
    base = dict(name="ecg", image=IMAGE, mode="sync", resources=RES, api_resources=API_RES)
    base.update(kw)
    return ModelSpec(**base)


def container(dep):
    return dep["spec"]["template"]["spec"]["containers"][0]


# ───────────── manifests ─────────────
def test_sync_deployment_limits_probes_and_hardening():
    dep = m.deployment(spec(env={"HF_TOKEN": "t"}), "main", S, 1)
    c, pod_spec = container(dep), dep["spec"]["template"]["spec"]
    assert dep["metadata"]["name"] == "m-ecg" and dep["spec"]["replicas"] == 1
    assert c["image"] == IMAGE
    assert c["resources"]["requests"] == c["resources"]["limits"] == {"cpu": "1500m", "memory": str(4 * GI)}
    assert "startupProbe" in c and c["startupProbe"]["failureThreshold"] * c["startupProbe"]["periodSeconds"] >= 1800
    assert c["readinessProbe"]["httpGet"]["path"] == "/health"
    assert c["securityContext"]["runAsNonRoot"] is True and c["securityContext"]["allowPrivilegeEscalation"] is False
    assert c["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["imagePullSecrets"] == [{"name": "ghcr-pull"}]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["HF_TOKEN"] == "t" and env["FASTMLAPI_ROLE"] == "sync" and env["HF_HOME"] == "/cache/hf"
    assert pod_spec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "m-ecg-cache"
    assert dep["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}


def test_model_env_cannot_override_platform_variables():
    env = {e["name"]: e["value"] for e in container(m.deployment(spec(env={"FASTMLAPI_ROLE": "worker", "HF_HOME": "/x"}), "main", S, 1))["env"]}
    assert env["FASTMLAPI_ROLE"] == "sync" and env["HF_HOME"] == "/cache/hf"


def test_gpu_limit_only_when_approved_and_pull_secret_only_for_ghcr():
    assert "nvidia.com/gpu" not in container(m.deployment(spec(), "main", S, 1))["resources"]["limits"]
    gpu_dep = m.deployment(spec(resources=GPU), "main", S, 1)
    assert container(gpu_dep)["resources"]["limits"]["nvidia.com/gpu"] == "1"
    local = m.deployment(spec(image="sha256:" + "b" * 64), "main", S, 1)
    assert "imagePullSecrets" not in local["spec"]["template"]["spec"]


def test_secret_refs_become_env_from():
    c = container(m.deployment(spec(secret_refs=("hf-token", "db")), "main", S, 1))
    assert c["envFrom"] == [{"secretRef": {"name": "hf-token"}}, {"secretRef": {"name": "db"}}]


def test_queue_api_and_worker_deployments():
    q = spec(mode="queue", resources=GPU, queue_url="redis://m_ecg:pw@redis:6379/0", max_job_seconds=900)
    api, worker = m.deployment(q, "api", S, 1), m.deployment(q, "worker", S, 0)
    assert api["metadata"]["name"] == "m-ecg-api" and worker["metadata"]["name"] == "m-ecg-worker"
    assert container(api)["resources"]["limits"] == {"cpu": "100m", "memory": str(256 * 1024 * 1024)}
    assert "volumeMounts" not in container(api)
    assert container(worker)["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert "ports" not in container(worker) and "readinessProbe" not in container(worker)
    assert worker["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 900
    assert worker["spec"]["strategy"] == {"type": "Recreate"}
    assert worker["spec"]["replicas"] == 0
    for dep, role in ((api, "api"), (worker, "worker")):
        env = {e["name"]: e["value"] for e in container(dep)["env"]}
        assert env["FASTMLAPI_ROLE"] == role and env["FASTMLAPI_QUEUE_URL"] == "redis://m_ecg:pw@redis:6379/0"


def test_service_pvc_and_network_policy():
    svc, claim, policy = m.service(spec(), S), m.pvc(spec(), S), m.network_policy(spec(), S)
    assert svc["metadata"]["name"] == "m-ecg" and svc["spec"]["selector"]["mlapi/role"] == "main"
    assert claim["spec"]["resources"]["requests"]["storage"] == str(10 * GI) and claim["spec"]["storageClassName"] == "fast"
    sources = policy["spec"]["ingress"][0]["from"]
    assert {tuple(s["podSelector"]["matchLabels"].items())[0] for s in sources} == {("app", "mlapi-router"), ("app", "mlapi-supervisor")}
    egress_blocks = [t["ipBlock"] for rule in policy["spec"]["egress"] for t in rule["to"] if "ipBlock" in t]
    assert egress_blocks[0]["cidr"] == "0.0.0.0/0" and "10.0.0.0/8" in egress_blocks[0]["except"]
    redis_rules = [r for r in policy["spec"]["egress"] if any("redis" in str(t) for t in r["to"])]
    assert redis_rules == []                                           # sync models never need Redis
    queue_policy = m.network_policy(spec(mode="queue"), S)
    assert any("mlapi-queue-redis" in str(r) for r in queue_policy["spec"]["egress"])


def test_manifests_serialise_to_valid_yaml():
    for body in (m.deployment(spec(), "main", S, 1), m.service(spec(), S), m.pvc(spec(), S), m.network_policy(spec(), S)):
        assert yaml.safe_load(yaml.safe_dump(body)) == body


# ───────────── backend ─────────────
@pytest.fixture
def api():
    return K8sApi(apps=FakeApps(), core=FakeCore(), net=FakeNet())


@pytest.fixture
def backend(api):
    return KubernetesBackend(S, api=api)


async def test_apply_creates_everything_for_a_sync_model(backend, api):
    await backend.apply_model(spec())
    assert list(api.apps.deployments) == ["m-ecg"] and "m-ecg" in api.core.services
    assert "m-ecg-cache" in api.core.pvcs and "m-ecg" in api.net.policies
    assert await backend.endpoint("ecg") == "http://m-ecg.mlapi-models.svc:8000"


async def test_reapply_replaces_deployment_but_keeps_the_cache_claim(backend, api):
    await backend.apply_model(spec())
    await backend.apply_model(spec(image="ghcr.io/org/ecg@sha256:" + "b" * 64))
    assert ("replace_deployment", "m-ecg") in api.apps.calls
    assert container(api.apps.deployments["m-ecg"].spec.body)["image"].endswith("b" * 64)
    assert [c for c in api.core.calls if c[0] == "create_pvc"] == [("create_pvc", "m-ecg-cache"), ("create_pvc", "m-ecg-cache")]
    assert len(api.core.pvcs) == 1


async def test_reapply_wakes_a_sleeping_sync_model(backend, api):
    await backend.apply_model(spec())
    await backend.scale("ecg", 0)
    await backend.apply_model(spec())
    assert api.apps.deployments["m-ecg"].spec.replicas == 1


async def test_queue_mode_creates_api_and_idle_worker_and_reapply_keeps_worker_replicas(backend, api):
    await backend.apply_model(spec(mode="queue"))
    assert set(api.apps.deployments) == {"m-ecg-api", "m-ecg-worker"}
    assert api.apps.deployments["m-ecg-api"].spec.replicas == 1 and api.apps.deployments["m-ecg-worker"].spec.replicas == 0
    await backend.scale("ecg", 1, role="worker")
    await backend.apply_model(spec(mode="queue", image="ghcr.io/org/ecg@sha256:" + "c" * 64))
    assert api.apps.deployments["m-ecg-worker"].spec.replicas == 1


async def test_scale_targets_the_right_workload_and_unknown_fails(backend, api):
    await backend.apply_model(spec(mode="queue"))
    await backend.scale("ecg", 1, role="worker")
    await backend.scale("ecg", 0)
    assert ("scale", "m-ecg-worker", 1) in api.apps.calls and ("scale", "m-ecg-api", 0) in api.apps.calls
    with pytest.raises(BackendError):
        await backend.scale("nope", 1)


async def test_status_ready_after_rollout_and_not_ready_during_one(backend, api):
    assert (await backend.status("ecg")).exists is False
    await backend.apply_model(spec())
    s = await backend.status("ecg")
    assert (s.exists, s.ready, s.replicas) == (True, True, 1)
    dep = api.apps.deployments["m-ecg"]
    dep.status.updated_replicas = 0                      # new revision not rolled out yet
    assert (await backend.status("ecg")).ready is False
    dep.status.updated_replicas, dep.status.observed_generation = 1, 0
    assert (await backend.status("ecg")).ready is False  # controller has not seen the new generation
    dep.status.observed_generation = dep.metadata.generation
    dep.status.available_replicas = 0
    assert (await backend.status("ecg")).ready is False


async def test_status_pending_reasons_and_restarts(backend, api):
    await backend.apply_model(spec(resources=GPU))
    api.core.pods = [pod(phase="Pending", unschedulable="0/1 nodes: 1 Insufficient nvidia.com/gpu.")]
    assert (await backend.status("ecg")).pending_reason == "unschedulable: no free GPU slice"
    api.core.pods = [pod(phase="Pending", unschedulable="0/1 nodes: taints")]
    assert "taints" in (await backend.status("ecg")).pending_reason
    api.core.pods = [pod(phase="Pending", waiting="ImagePullBackOff")]
    assert "image pull failed" in (await backend.status("ecg")).pending_reason
    api.core.pods = [pod(restarts=4), pod(role="worker", restarts=9)]
    s = await backend.status("ecg")
    assert s.restarts == 4 and s.pending_reason is None   # worker restarts are not counted against the API


async def test_queue_status_counts_ready_workers(backend, api):
    await backend.apply_model(spec(mode="queue"))
    assert (await backend.status("ecg")).worker_replicas == 0
    await backend.scale("ecg", 1, role="worker")
    assert (await backend.status("ecg")).worker_replicas == 1


async def test_delete_removes_resources_and_optionally_the_claim(backend, api):
    await backend.apply_model(spec(mode="queue"))
    await backend.delete_model("ecg", keep_cache=True)
    assert api.apps.deployments == {} and api.core.services == {} and api.net.policies == {}
    assert "m-ecg-cache" in api.core.pvcs
    await backend.delete_model("ecg")                   # idempotent, and now drops the claim
    assert api.core.pvcs == {}


async def test_list_models(backend, api):
    await backend.apply_model(spec(name="a"))
    await backend.apply_model(spec(name="b", mode="queue"))
    assert await backend.list_models() == ["a", "b"]


async def test_cluster_errors_become_backend_errors(api):
    async def boom(*a, **k):
        raise RuntimeError("forbidden")

    api.apps.create_namespaced_deployment = boom
    with pytest.raises(BackendError):
        await KubernetesBackend(S, api=api).apply_model(spec())
