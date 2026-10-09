"""Pure functions that build Kubernetes manifests (plain dicts) for a model. No cluster access here."""
from app.cluster.backend import ModelSpec
from app.config import Settings
from app.registry.resources import Resources

MANAGED_BY = {"app.kubernetes.io/managed-by": "mlapi-supervisor"}
# a generous startup window: first starts download model weights
STARTUP_FAILURE_THRESHOLD = 180
STARTUP_PERIOD_SECONDS = 10


def selector(text: str) -> dict[str, str]:
    key, _, value = text.partition("=")
    return {key: value}


def names(model: str) -> dict[str, str]:
    return {
        "main": f"m-{model}",
        "api": f"m-{model}-api",
        "worker": f"m-{model}-worker",
        "service": f"m-{model}",
        "pvc": f"m-{model}-cache",
        "policy": f"m-{model}",
    }


def labels(model: str, role: str) -> dict[str, str]:
    return {**MANAGED_BY, "mlapi/model": model, "mlapi/role": role}


def resource_block(res: Resources, *, gpu: bool) -> dict:
    values = {"cpu": f"{res.cpu_m}m", "memory": str(res.memory_bytes)}
    limits = dict(values)
    if gpu:
        limits["nvidia.com/gpu"] = "1"
    return {"requests": values, "limits": limits}  # requests == limits: predictable QoS


def _env(spec: ModelSpec, fastmlapi_role: str, cache: bool) -> list[dict]:
    env = {k: v for k, v in spec.env.items() if k not in ("FASTMLAPI_ROLE", "FASTMLAPI_CACHE_DIR", "HF_HOME", "FASTMLAPI_QUEUE_URL")}
    env["FASTMLAPI_ROLE"] = fastmlapi_role  # platform variables always win
    if cache:
        env["FASTMLAPI_CACHE_DIR"] = "/cache"
        env["HF_HOME"] = "/cache/hf"
    if spec.queue_url:
        env["FASTMLAPI_QUEUE_URL"] = spec.queue_url
    return [{"name": k, "value": v} for k, v in sorted(env.items())]


def deployment(spec: ModelSpec, role: str, settings: Settings, replicas: int) -> dict:
    """role: 'main' (sync model), 'api' (queue API) or 'worker' (queue worker)."""
    n = names(spec.name)
    queue = spec.mode == "queue"
    is_worker = role == "worker"
    fastmlapi_role = "worker" if is_worker else ("api" if role == "api" else "sync")
    resources = spec.resources if not role == "api" else (spec.api_resources or spec.resources)
    gpu = resources.gpu and role != "api"
    cache = role != "api"
    pod_labels = labels(spec.name, "worker" if is_worker else "main")

    container: dict = {
        "name": "model",
        "image": spec.image,
        "env": _env(spec, fastmlapi_role, cache),
        "resources": resource_block(resources, gpu=gpu),
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "runAsNonRoot": True,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
    }
    if spec.secret_refs:
        container["envFrom"] = [{"secretRef": {"name": ref}} for ref in spec.secret_refs]
    if cache:
        container["volumeMounts"] = [{"name": "cache", "mountPath": "/cache"}]
    if not is_worker:  # workers have no HTTP port
        container["ports"] = [{"containerPort": 8000, "name": "http"}]
        container["startupProbe"] = {
            "httpGet": {"path": "/health", "port": 8000},
            "periodSeconds": STARTUP_PERIOD_SECONDS,
            "failureThreshold": STARTUP_FAILURE_THRESHOLD,
        }
        container["readinessProbe"] = {"httpGet": {"path": "/health", "port": 8000}, "periodSeconds": 10, "timeoutSeconds": 5}
        container["livenessProbe"] = {"tcpSocket": {"port": 8000}, "periodSeconds": 20, "failureThreshold": 6}

    pod_spec: dict = {
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "terminationGracePeriodSeconds": spec.max_job_seconds if is_worker else 30,
        "containers": [container],
    }
    if cache:
        pod_spec["volumes"] = [{"name": "cache", "persistentVolumeClaim": {"claimName": n["pvc"]}}]
    if settings.k8s_image_pull_secret and spec.image.startswith("ghcr.io/"):
        pod_spec["imagePullSecrets"] = [{"name": settings.k8s_image_pull_secret}]

    # the worker holds the GPU and a running job: replace it only after the old one is gone
    strategy = {"type": "Recreate"} if is_worker else {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    name = n["worker"] if is_worker else (n["api"] if queue else n["main"])
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": settings.k8s_namespace, "labels": pod_labels},
        "spec": {
            "replicas": replicas,
            "strategy": strategy,
            "selector": {"matchLabels": pod_labels},
            "template": {"metadata": {"labels": pod_labels}, "spec": pod_spec},
        },
    }


def service(spec: ModelSpec, settings: Settings) -> dict:
    n = names(spec.name)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": n["service"], "namespace": settings.k8s_namespace, "labels": labels(spec.name, "main")},
        "spec": {
            "type": "ClusterIP",
            "selector": labels(spec.name, "main"),
            "ports": [{"name": "http", "port": 8000, "targetPort": 8000}],
        },
    }


def pvc(spec: ModelSpec, settings: Settings) -> dict:
    size = max(spec.resources.disk_bytes, 1)
    body: dict = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": names(spec.name)["pvc"], "namespace": settings.k8s_namespace, "labels": labels(spec.name, "cache")},
        "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": str(size)}}},
    }
    if settings.storage_class:
        body["spec"]["storageClassName"] = settings.storage_class
    return body


def network_policy(spec: ModelSpec, settings: Settings) -> dict:
    """Only the Router and the Supervisor may reach a model; models may reach the internet and (queue mode) Redis."""
    private = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]
    egress = [
        {"to": [{"namespaceSelector": {}, "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
         "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
        {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": private}}]},
    ]
    if spec.mode == "queue":
        egress.append({"to": [{"namespaceSelector": {}, "podSelector": {"matchLabels": selector(settings.redis_selector)}}],
                       "ports": [{"protocol": "TCP", "port": 6379}]})
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": names(spec.name)["policy"], "namespace": settings.k8s_namespace, "labels": labels(spec.name, "main")},
        "spec": {
            "podSelector": {"matchLabels": {"mlapi/model": spec.name}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{
                "from": [
                    {"namespaceSelector": {}, "podSelector": {"matchLabels": selector(settings.router_selector)}},
                    {"namespaceSelector": {}, "podSelector": {"matchLabels": selector(settings.supervisor_selector)}},
                ],
                "ports": [{"protocol": "TCP", "port": 8000}],
            }],
            "egress": egress,
        },
    }
