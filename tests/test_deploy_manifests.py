from pathlib import Path

import pytest
import yaml

DIR = Path(__file__).resolve().parent.parent / "deploy" / "k8s"


def docs():
    out = []
    for path in sorted(DIR.glob("*.yaml")):
        out.extend((path.name, d) for d in yaml.safe_load_all(path.read_text()) if d)
    return out


def by_kind(kind):
    return [d for _, d in docs() if d["kind"] == kind]


def test_every_file_parses_and_has_kind_and_name():
    assert len(docs()) >= 12
    for name, d in docs():
        assert d.get("kind") and d.get("metadata", {}).get("name"), name


def test_supervisor_rbac_is_namespaced_and_never_cluster_admin():
    assert by_kind("ClusterRole") == [] and by_kind("ClusterRoleBinding") == []
    role = by_kind("Role")[0]
    assert role["metadata"]["namespace"] == "mlapi-models"
    for rule in role["rules"]:
        assert "*" not in rule["verbs"] and "*" not in rule["resources"]
    resources = {r for rule in role["rules"] for r in rule["resources"]}
    assert "secrets" not in resources and "pods/exec" not in resources
    binding = by_kind("RoleBinding")[0]
    assert binding["subjects"][0]["namespace"] == "mlapi-system" and binding["roleRef"]["name"] == role["metadata"]["name"]


def test_models_namespace_has_quota_limit_range_and_gpu_cap():
    quota = by_kind("ResourceQuota")[0]
    assert quota["metadata"]["namespace"] == "mlapi-models"
    assert "requests.nvidia.com/gpu" in quota["spec"]["hard"] and "limits.memory" in quota["spec"]["hard"]
    assert by_kind("LimitRange")[0]["spec"]["limits"][0]["max"]["memory"]


def test_supervisor_is_single_instance_and_not_exposed():
    dep = next(d for d in by_kind("Deployment") if d["metadata"]["name"] == "mlapi-supervisor")
    assert dep["spec"]["replicas"] == 1 and dep["spec"]["strategy"]["type"] == "Recreate"
    svc = next(d for d in by_kind("Service") if d["metadata"]["name"] == "mlapi-supervisor")
    assert svc["spec"]["type"] == "ClusterIP"
    assert by_kind("Ingress") == []
    env = {e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["CLUSTER_BACKEND"] == "kubernetes" and env["K8S_NAMESPACE"] == "mlapi-models"


def test_router_has_no_service_account_token_and_labels_match_network_policy():
    dep = next(d for d in by_kind("Deployment") if d["metadata"]["name"] == "mlapi-router")
    assert dep["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert dep["spec"]["template"]["metadata"]["labels"] == {"app": "mlapi-router"}   # = Settings.router_selector


def test_redis_has_a_password_and_no_persistence():
    dep = next(d for d in by_kind("Deployment") if d["metadata"]["name"] == "mlapi-redis")
    args = dep["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--requirepass" in args and args[args.index("--appendonly") + 1] == "no" and args[args.index("--save") + 1] == ""
    assert dep["spec"]["template"]["metadata"]["labels"] == {"app": "mlapi-redis"}


@pytest.mark.parametrize("name", ["mlapi-supervisor", "mlapi-router", "mlapi-redis"])
def test_system_containers_are_hardened(name):
    dep = next(d for d in by_kind("Deployment") if d["metadata"]["name"] == name)
    sec = dep["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    assert sec["allowPrivilegeEscalation"] is False and sec["runAsNonRoot"] is True and sec["capabilities"]["drop"] == ["ALL"]


def test_time_slicing_config_advertises_four_replicas():
    cm = by_kind("ConfigMap")[0]
    assert yaml.safe_load(cm["data"]["any"])["sharing"]["timeSlicing"]["resources"][0]["replicas"] == 4
