import logging
from dataclasses import dataclass

from app.cluster import k8s_manifests as m
from app.cluster.backend import BackendError, ModelSpec, RuntimeStatus
from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class K8sApi:
    apps: object
    core: object
    net: object
    close: object = None


async def connect(settings: Settings) -> K8sApi:
    """Creates API clients: in-cluster service account, or KUBECONFIG when configured."""
    from kubernetes_asyncio import client, config

    if settings.kubeconfig:
        await config.load_kube_config(config_file=settings.kubeconfig)
    else:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()
    api_client = client.ApiClient()
    return K8sApi(
        apps=client.AppsV1Api(api_client),
        core=client.CoreV1Api(api_client),
        net=client.NetworkingV1Api(api_client),
        close=api_client.close,
    )


def _is(exc: Exception, status: int) -> bool:
    return getattr(exc, "status", None) == status


class KubernetesBackend:
    """Runs models as Deployments + Service + PVC + NetworkPolicy in a single namespace."""

    def __init__(self, settings: Settings, api: K8sApi | None = None):
        self._s = settings
        self._ns = settings.k8s_namespace
        self._api = api

    async def _get_api(self) -> K8sApi:
        if self._api is None:
            self._api = await connect(self._s)
        return self._api

    async def aclose(self) -> None:
        if self._api is not None and self._api.close is not None:
            await self._api.close()

    # ───────────── helpers ─────────────
    async def _create_or_replace(self, create, replace, name: str, body: dict) -> None:
        try:
            await create(self._ns, body)
        except Exception as exc:
            if not _is(exc, 409):
                raise
            await replace(name, self._ns, body)

    async def _current_replicas(self, api: K8sApi, name: str) -> int | None:
        try:
            deployment = await api.apps.read_namespaced_deployment(name, self._ns)
        except Exception as exc:
            if _is(exc, 404):
                return None
            raise
        return deployment.spec.replicas

    # ───────────── interface ─────────────
    async def apply_model(self, spec: ModelSpec) -> None:
        api = await self._get_api()
        n = m.names(spec.name)
        try:
            await self._ensure_pvc(api, spec)
            await self._create_or_replace(
                api.net.create_namespaced_network_policy, api.net.replace_namespaced_network_policy,
                n["policy"], m.network_policy(spec, self._s),
            )
            await self._create_or_replace(
                api.core.create_namespaced_service, api.core.replace_namespaced_service,
                n["service"], self._service_body(spec),
            )
            roles = [("api", n["api"], 1), ("worker", n["worker"], 0)] if spec.mode == "queue" else [("main", n["main"], 1)]
            for role, name, default_replicas in roles:
                existing = await self._current_replicas(api, name)
                # a deploy always starts the model; only a queue worker keeps its current replica count
                replicas = existing if (role == "worker" and existing is not None) else default_replicas
                body = m.deployment(spec, role, self._s, replicas)
                await self._create_or_replace(
                    api.apps.create_namespaced_deployment, api.apps.replace_namespaced_deployment, name, body
                )
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"kubernetes error: {exc}") from exc

    def _service_body(self, spec: ModelSpec) -> dict:
        body = m.service(spec, self._s)
        return body

    async def _ensure_pvc(self, api: K8sApi, spec: ModelSpec) -> None:
        try:
            await api.core.create_namespaced_persistent_volume_claim(self._ns, m.pvc(spec, self._s))
        except Exception as exc:
            if not _is(exc, 409):  # an existing claim is kept: its spec is immutable and it holds the cache
                raise

    async def scale(self, name: str, replicas: int, role: str = "main") -> None:
        api = await self._get_api()
        n = m.names(name)
        candidates = [n["worker"]] if role == "worker" else [n["api"], n["main"]]
        for deployment_name in candidates:
            try:
                await api.apps.patch_namespaced_deployment_scale(
                    deployment_name, self._ns, {"spec": {"replicas": max(replicas, 0)}}
                )
                return
            except Exception as exc:
                if not _is(exc, 404):
                    raise BackendError(f"kubernetes error: {exc}") from exc
        raise BackendError(f"no {role} workload for model {name!r}")

    async def delete_model(self, name: str, *, keep_cache: bool = False) -> None:
        api = await self._get_api()
        n = m.names(name)
        calls = [
            (api.apps.delete_namespaced_deployment, n["main"]),
            (api.apps.delete_namespaced_deployment, n["api"]),
            (api.apps.delete_namespaced_deployment, n["worker"]),
            (api.core.delete_namespaced_service, n["service"]),
            (api.net.delete_namespaced_network_policy, n["policy"]),
        ]
        if not keep_cache:
            calls.append((api.core.delete_namespaced_persistent_volume_claim, n["pvc"]))
        for call, resource in calls:
            try:
                await call(resource, self._ns)
            except Exception as exc:
                if not _is(exc, 404):
                    raise BackendError(f"kubernetes error: {exc}") from exc

    async def status(self, name: str) -> RuntimeStatus:
        api = await self._get_api()
        n = m.names(name)
        try:
            main = None
            for candidate in (n["api"], n["main"]):
                try:
                    main = await api.apps.read_namespaced_deployment(candidate, self._ns)
                    break
                except Exception as exc:
                    if not _is(exc, 404):
                        raise
            if main is None:
                return RuntimeStatus(exists=False)
            worker_replicas = 0
            try:
                worker = await api.apps.read_namespaced_deployment(n["worker"], self._ns)
                worker_replicas = worker.status.ready_replicas or 0
            except Exception as exc:
                if not _is(exc, 404):
                    raise
            pods = await api.core.list_namespaced_pod(self._ns, label_selector=f"mlapi/model={name}")
            return RuntimeStatus(
                exists=True,
                ready=self._rolled_out(main),
                replicas=main.spec.replicas or 0,
                worker_replicas=worker_replicas,
                pending_reason=self._pending_reason(pods.items),
                restarts=self._restarts(pods.items),
            )
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"kubernetes error: {exc}") from exc

    @staticmethod
    def _rolled_out(deployment) -> bool:
        """The standard 'rollout complete' test: the newest spec is fully rolled out and available."""
        spec_replicas = deployment.spec.replicas or 0
        st = deployment.status
        if spec_replicas == 0:
            return False
        return (
            (st.observed_generation or 0) >= (deployment.metadata.generation or 0)
            and (st.updated_replicas or 0) == spec_replicas
            and (st.replicas or 0) == spec_replicas
            and (st.available_replicas or 0) == spec_replicas
        )

    @staticmethod
    def _pending_reason(pods) -> str | None:
        for pod in pods:
            if pod.status.phase != "Pending":
                continue
            for cond in pod.status.conditions or []:
                if cond.type == "PodScheduled" and cond.status == "False":
                    message = cond.message or ""
                    if "nvidia.com/gpu" in message:
                        return "unschedulable: no free GPU slice"
                    return f"unschedulable: {message[:200]}"
            for cs in pod.status.container_statuses or []:
                waiting = cs.state.waiting if cs.state else None
                if waiting and waiting.reason in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName"):
                    return f"image pull failed: {waiting.reason}"
        return None

    @staticmethod
    def _restarts(pods) -> int:
        total = 0
        for pod in pods:
            if (pod.metadata.labels or {}).get("mlapi/role") == "main":
                total += sum(cs.restart_count or 0 for cs in (pod.status.container_statuses or []))
        return total

    async def endpoint(self, name: str) -> str:
        return f"http://{m.names(name)['service']}.{self._ns}.svc:8000"

    async def list_models(self) -> list[str]:
        api = await self._get_api()
        try:
            deployments = await api.apps.list_namespaced_deployment(
                self._ns, label_selector="app.kubernetes.io/managed-by=mlapi-supervisor"
            )
        except Exception as exc:
            raise BackendError(f"kubernetes error: {exc}") from exc
        return sorted({d.metadata.labels["mlapi/model"] for d in deployments.items if "mlapi/model" in (d.metadata.labels or {})})
