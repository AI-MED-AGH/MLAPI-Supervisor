from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
K8S = ROOT / "deploy" / "k8s"


def compose(*names):
    merged = {"services": {}, "networks": {}}
    for name in names:
        doc = yaml.safe_load((ROOT / name).read_text())
        for service, body in doc.get("services", {}).items():
            merged["services"].setdefault(service, {}).update(body)
        merged["networks"].update(doc.get("networks", {}))
    return merged


def manifests():
    out = []
    for path in sorted(K8S.glob("*.yaml")):
        out.extend(d for d in yaml.safe_load_all(path.read_text()) if d)
    return out


def named(kind, name):
    found = [d for d in manifests() if d["kind"] == kind and d["metadata"]["name"] == name]
    assert found, f"no {kind} {name}"
    return found[0]


# ── Docker Compose: model containers must not be able to reach the database or the key store ──


def test_the_key_store_and_database_are_not_on_the_network_models_run_on():
    doc = compose("docker-compose.yml", "docker-compose.postgres.yml")
    assert doc["services"]["redis"]["networks"] == ["control"]
    assert doc["services"]["db"]["networks"] == ["control"]
    assert doc["networks"]["control"]["internal"] is True  # no route out either


def test_only_the_queue_redis_shares_the_models_network_besides_the_platform_itself():
    doc = compose("docker-compose.yml")
    on_models = {name for name, s in doc["services"].items() if "models" in s["networks"]}
    assert on_models == {"queue-redis", "supervisor", "router"}
    assert set(doc["services"]["supervisor"]["networks"]) == {"control", "models"}
    assert set(doc["services"]["router"]["networks"]) == {"control", "models"}


# ── Kubernetes ──


def test_the_router_is_exposed_through_an_ingress_with_edge_limits():
    ingress = named("Ingress", "mlapi-router")
    notes = ingress["metadata"]["annotations"]
    assert int(notes["nginx.ingress.kubernetes.io/limit-rps"]) > 0
    assert int(notes["nginx.ingress.kubernetes.io/limit-connections"]) > 0
    assert notes["nginx.ingress.kubernetes.io/proxy-body-size"] == "10m"  # the Router's own MAX_BODY_BYTES
    assert ingress["spec"]["tls"], "the Router must only be offered over TLS"
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == "mlapi-router"


def test_the_router_survives_a_node_drain_and_scales_with_load():
    pdb = named("PodDisruptionBudget", "mlapi-router")
    assert pdb["spec"]["minAvailable"] >= 1
    hpa = named("HorizontalPodAutoscaler", "mlapi-router")
    assert hpa["spec"]["minReplicas"] >= 2 and hpa["spec"]["maxReplicas"] > hpa["spec"]["minReplicas"]
    assert hpa["spec"]["scaleTargetRef"]["name"] == "mlapi-router"


def test_the_key_store_accepts_connections_only_from_the_router_and_the_supervisor():
    policy = named("NetworkPolicy", "mlapi-redis")
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "mlapi-redis"}
    allowed = {
        p["podSelector"]["matchLabels"]["app"]
        for rule in policy["spec"]["ingress"] for p in rule["from"]
    }
    assert allowed == {"mlapi-router", "mlapi-supervisor"}
    assert all("namespaceSelector" not in p for rule in policy["spec"]["ingress"] for p in rule["from"])


def test_the_queue_redis_is_reachable_by_models_and_the_supervisor_only():
    policy = named("NetworkPolicy", "mlapi-queue-redis")
    sources = [p for rule in policy["spec"]["ingress"] for p in rule["from"]]
    namespaces = {p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] for p in sources if "namespaceSelector" in p}
    assert namespaces == {"mlapi-models", "mlapi-system"}


def test_nothing_in_the_cluster_can_open_the_admin_api():
    policy = named("NetworkPolicy", "mlapi-supervisor")
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "mlapi-supervisor"}
    assert policy["spec"].get("ingress", []) == []  # kubectl port-forward is not subject to NetworkPolicy
    assert "Ingress" in policy["spec"]["policyTypes"]


def test_compose_gives_every_model_its_own_network_and_names_the_platform_containers_to_attach():
    doc = compose("docker-compose.yml")
    env = doc["services"]["supervisor"]["environment"]
    assert str(env["DOCKER_ISOLATE_MODELS"]).lower() == "true"
    peers = {name.strip() for name in env["DOCKER_PEERS"].split(",")}
    assert peers == {doc["services"]["router"]["container_name"], doc["services"]["supervisor"]["container_name"]}
