from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DeploymentSpec:
    model_id: str
    image: str
    replicas: int
    container_port: int
    service_port: int
    env: dict[str, str]


class KubernetesService:
    def __init__(
        self,
        *,
        namespace: str,
        service_type: str = "LoadBalancer",
        image_pull_secret_name: str | None = None,
    ) -> None:
        self.namespace = namespace
        self.service_type = service_type
        self.image_pull_secret_name = image_pull_secret_name
        self._apps_api: client.AppsV1Api | None = None
        self._core_api: client.CoreV1Api | None = None
        self._custom_api: client.CustomObjectsApi | None = None

    def ensure_registry_pull_secret(
        self, *, username: str, token: str, registry: str
    ) -> None:
        if not username or not token:
            raise ValueError(
                "username and token are required to create image pull secret"
            )

        if not self.image_pull_secret_name:
            raise ValueError("image_pull_secret_name is not configured")

        self._ensure_namespace()
        core_api = self._core_api_client

        auth = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("utf-8")
        docker_config = {
            "auths": {
                registry: {
                    "username": username,
                    "password": token,
                    "auth": auth,
                }
            }
        }
        docker_config_base64 = base64.b64encode(
            json.dumps(docker_config).encode("utf-8")
        ).decode("utf-8")

        secret_body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=self.image_pull_secret_name),
            type="kubernetes.io/dockerconfigjson",
            data={".dockerconfigjson": docker_config_base64},
        )

        try:
            core_api.read_namespaced_secret(self.image_pull_secret_name, self.namespace)
            core_api.replace_namespaced_secret(
                name=self.image_pull_secret_name,
                namespace=self.namespace,
                body=secret_body,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            core_api.create_namespaced_secret(
                namespace=self.namespace, body=secret_body
            )

    def upsert_model(self, spec: DeploymentSpec) -> dict[str, Any]:
        self._ensure_namespace()

        deployment_name = self._deployment_name(spec.model_id)
        service_name = self._service_name(spec.model_id)
        deployment_body = self._build_deployment_body(deployment_name, spec)
        service_body = self._build_service_body(service_name, spec)

        apps_api = self._apps_api_client
        core_api = self._core_api_client

        try:
            apps_api.read_namespaced_deployment(
                name=deployment_name, namespace=self.namespace
            )
            apps_api.patch_namespaced_deployment(
                name=deployment_name,
                namespace=self.namespace,
                body=deployment_body,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            apps_api.create_namespaced_deployment(
                namespace=self.namespace, body=deployment_body
            )

        try:
            core_api.read_namespaced_service(
                name=service_name, namespace=self.namespace
            )
            core_api.patch_namespaced_service(
                name=service_name,
                namespace=self.namespace,
                body=service_body,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            core_api.create_namespaced_service(
                namespace=self.namespace, body=service_body
            )

        return self.get_model_status(spec.model_id)

    def delete_model(self, model_id: str) -> bool:
        deployment_name = self._deployment_name(model_id)
        service_name = self._service_name(model_id)
        deleted_any = False

        apps_api = self._apps_api_client
        core_api = self._core_api_client

        try:
            apps_api.delete_namespaced_deployment(
                name=deployment_name, namespace=self.namespace
            )
            deleted_any = True
        except ApiException as exc:
            if exc.status != 404:
                raise

        try:
            core_api.delete_namespaced_service(
                name=service_name, namespace=self.namespace
            )
            deleted_any = True
        except ApiException as exc:
            if exc.status != 404:
                raise

        return deleted_any

    def get_model_status(self, model_id: str) -> dict[str, Any]:
        deployment_name = self._deployment_name(model_id)
        service_name = self._service_name(model_id)

        apps_api = self._apps_api_client
        core_api = self._core_api_client

        try:
            deployment = apps_api.read_namespaced_deployment(
                deployment_name, self.namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                raise KeyError(f"Model deployment '{model_id}' was not found") from exc
            raise

        service = None
        try:
            service = core_api.read_namespaced_service(service_name, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

        desired_replicas = deployment.spec.replicas or 0
        ready_replicas = deployment.status.ready_replicas or 0
        available_replicas = deployment.status.available_replicas or 0
        phase = (
            "ready"
            if available_replicas >= desired_replicas and desired_replicas > 0
            else "deploying"
        )

        return {
            "model_id": model_id,
            "deployment_name": deployment_name,
            "service_name": service_name,
            "namespace": self.namespace,
            "phase": phase,
            "desired_replicas": desired_replicas,
            "ready_replicas": ready_replicas,
            "available_replicas": available_replicas,
            "service_type": self.service_type,
            "external_endpoints": self._service_endpoints(service),
        }

    def get_deployment_image(self, model_id: str) -> str | None:
        deployment_name = self._deployment_name(model_id)
        try:
            deployment = self._apps_api_client.read_namespaced_deployment(
                deployment_name, self.namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

        containers = deployment.spec.template.spec.containers or []
        if not containers:
            return None
        return containers[0].image

    def wait_for_rollout(
        self, model_id: str, *, timeout_seconds: float, poll_interval: float = 2.0
    ) -> None:
        deployment_name = self._deployment_name(model_id)
        deadline = time.monotonic() + timeout_seconds

        while True:
            deployment = self._apps_api_client.read_namespaced_deployment_status(
                name=deployment_name, namespace=self.namespace
            )
            generation = deployment.metadata.generation or 0
            observed = deployment.status.observed_generation or 0
            desired = deployment.spec.replicas or 0
            ready = deployment.status.ready_replicas or 0
            updated = deployment.status.updated_replicas or 0
            unavailable = deployment.status.unavailable_replicas or 0

            if (
                observed >= generation
                and updated >= desired
                and ready >= desired
                and unavailable == 0
                and desired > 0
            ):
                return

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Deployment '{deployment_name}' did not become ready within "
                    f"{timeout_seconds:.0f}s (ready={ready}/{desired}, unavailable={unavailable})"
                )

            time.sleep(poll_interval)

    def patch_deployment_image(self, model_id: str, image: str) -> None:
        deployment_name = self._deployment_name(model_id)
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "model", "image": image}],
                    }
                }
            }
        }
        self._apps_api_client.patch_namespaced_deployment(
            name=deployment_name, namespace=self.namespace, body=patch
        )

    def list_models(self) -> list[dict[str, Any]]:
        deployments = self._apps_api_client.list_namespaced_deployment(
            namespace=self.namespace,
            label_selector="managed-by=mlapi-supervisor",
        )

        items: list[dict[str, Any]] = []
        for deployment in deployments.items:
            labels = deployment.metadata.labels or {}
            model_id = labels.get("model-id")
            if not model_id:
                continue

            deployment_name = deployment.metadata.name
            service_name = self._service_name(model_id)

            service = None
            try:
                service = self._core_api_client.read_namespaced_service(
                    service_name, self.namespace
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise

            desired_replicas = deployment.spec.replicas or 0
            ready_replicas = deployment.status.ready_replicas or 0
            available_replicas = deployment.status.available_replicas or 0
            phase = (
                "ready"
                if available_replicas >= desired_replicas and desired_replicas > 0
                else "deploying"
            )

            items.append(
                {
                    "model_id": model_id,
                    "deployment_name": deployment_name,
                    "service_name": service_name,
                    "phase": phase,
                    "ready_replicas": ready_replicas,
                    "available_replicas": available_replicas,
                    "external_endpoints": self._service_endpoints(service),
                }
            )

        return items

    def get_models_cluster_status(self) -> list[dict[str, Any]]:
        """
        Gathers aggregate status for all managed models.
        Returns a list of structured JSON summaries with replica breakdowns and pod states.
        """
        deployments = self._apps_api_client.list_namespaced_deployment(
            namespace=self.namespace,
            label_selector="managed-by=mlapi-supervisor",
        )
        pods = self._core_api_client.list_namespaced_pod(
            namespace=self.namespace,
            label_selector="managed-by=mlapi-supervisor",
        )
        cpu_metrics = self._get_cpu_usage_map()

        pods_by_model: dict[str, list[client.V1Pod]] = {}
        for pod in pods.items:
            labels = pod.metadata.labels or {}
            model_id = labels.get("model-id")
            if model_id:
                pods_by_model.setdefault(model_id, []).append(pod)

        cluster_status: list[dict[str, Any]] = []

        for deployment in deployments.items:
            labels = deployment.metadata.labels or {}
            model_id = labels.get("model-id")
            if not model_id:
                continue

            model_pods = pods_by_model.get(model_id, [])
            parsed_pods = self._parse_pods(model_pods, cpu_metrics)
            replicas_info = self._extract_replica_counts(deployment)
            overall_status = self._evaluate_model_status(replicas_info, parsed_pods)
            cpu_usage = self._calculate_cpu_usage_for_model(cpu_metrics, model_pods)

            cluster_status.append(
                {
                    "name": model_id,
                    "status": overall_status,
                    "replicas": replicas_info,
                    "pods": parsed_pods,
                    "cpu_usage": cpu_usage,
                }
            )

        return cluster_status

    @classmethod
    def _parse_pods(
        cls, model_pods: list[client.V1Pod], cpu_metrics: dict[str, int]
    ) -> list[dict[str, Any]]:
        """Parses pods into [{name, phase, status, reason, cpu_usage}, ...]"""
        parsed_pods = []
        for pod in model_pods:
            try:
                parsed_pod = cls._parse_pod_info(pod, cpu_metrics)
            except Exception:
                logger.exception("Failed to parse kubernetes pod")
            else:
                parsed_pods.append(parsed_pod)
        return parsed_pods

    @staticmethod
    def _extract_replica_counts(deployment: client.V1Deployment) -> dict[str, int]:
        """Extracts desired, ready, and available replica numbers."""
        return {
            "desired": deployment.spec.replicas or 0,
            "ready": deployment.status.ready_replicas or 0,
            "available": deployment.status.available_replicas or 0,
        }

    @classmethod
    def _parse_pod_info(
        cls, pod: client.V1Pod, cpu_metrics: dict[str, int]
    ) -> dict[str, Any]:
        """
        Parses a single pod into {name, phase, state, reason, cpu_usage}.
        Throws if pod has no metadata or no name
        """
        assert pod.metadata is not None, (
            "Received a Pod without metadata from the Kubernetes API"
        )
        assert pod.metadata.name is not None, (
            "Received a Pod without name from the Kubernetes API"
        )

        phase, state, reason = cls._extract_container_state(pod)
        name = pod.metadata.name
        cpu_usage = cpu_metrics.get(name, 0)
        return {
            "name": name,
            "phase": phase,
            "state": state,
            "reason": reason,
            "cpu_usage": f"{cpu_usage}m",
        }

    @staticmethod
    def _extract_container_state(
        pod: client.V1Pod,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Inspects container statuses to extract execution state and failure reasons
        (e.g., CrashLoopBackOff, ImagePullBackOff, OOMKilled).
        """
        if pod.status is None:
            return None, None, None

        phase = pod.status.phase
        container_statuses = pod.status.container_statuses or []
        for cs in container_statuses:
            state = cs.state
            if state.waiting:
                return phase, "waiting", state.waiting.reason
            if state.terminated:
                return phase, "terminated", state.terminated.reason
            if state.running:
                return phase, "running", None

        return phase, "pending", None

    @staticmethod
    def _evaluate_model_status(
        replicas: dict[str, int], pods: list[dict[str, Any]]
    ) -> str:
        """Determines overall health: e.g. Running, Degraded, ScaledToZero, or error reason."""
        if replicas["desired"] == 0:
            return "ScaledToZero"

        if not pods:
            return "Pending"

        for pod in pods:
            if pod["reason"] in {
                "CrashLoopBackOff",
                "ImagePullBackOff",
                "ErrImagePull",
                "OOMKilled",
            }:
                return pod["reason"]

        if replicas["ready"] < replicas["desired"]:
            return "Degraded"

        return "Running"

    def _get_cpu_usage_map(self) -> dict[str, int]:
        """Queries Metrics Server and aggregates total nanocores per model into millicores."""
        try:
            metrics = self._custom_api_client.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=self.namespace,
                plural="pods",
                label_selector="managed-by=mlapi-supervisor",
            )
        except ApiException as exc:
            if (
                exc.status == 404
            ):  # if there is no metrics server then return nothing, but don't crash.
                return {}
            raise

        usage_map: dict[str, int] = {}
        for pod in metrics.get("items", []):
            metadata = pod.get("metadata", {})
            name = metadata.get("name")
            if not name:
                continue

            usage_map[name] = 0

            for container in pod.get("containers", []):
                usage_map[name] += self._extract_metric_data(container)

        return usage_map

    @staticmethod
    def _extract_metric_data(container: dict[str, Any]) -> int:
        cpu_str = container.get("usage", {}).get("cpu", "0m")
        if cpu_str.endswith("n"):
            return int(cpu_str[:-1]) // 1_000_000
        return int(cpu_str[:-1])

    @staticmethod
    def _calculate_cpu_usage_for_model(
        cpu_metrics: dict[str, int], pods: list[client.V1Pod]
    ) -> str:
        sum = 0
        for pod in pods:
            if pod.metadata and pod.metadata.name:
                sum += cpu_metrics.get(pod.metadata.name, 0)
        return f"{sum}m"

    @property
    def _apps_api_client(self) -> client.AppsV1Api:
        self._ensure_clients()
        return self._apps_api  # type: ignore[return-value]

    @property
    def _core_api_client(self) -> client.CoreV1Api:
        self._ensure_clients()
        return self._core_api  # type: ignore[return-value]

    @property
    def _custom_api_client(self) -> client.CustomObjectsApi:
        self._ensure_clients()
        return self._custom_api  # type: ignore[return-value]

    def _ensure_clients(self) -> None:
        if (
            self._apps_api is not None
            and self._core_api is not None
            and self._custom_api is not None
        ):
            return

        try:
            config.load_incluster_config()
        except ConfigException:
            try:
                config.load_kube_config()
            except ConfigException as exc:
                raise RuntimeError(
                    "Could not configure Kubernetes client. "
                    "Ensure kubeconfig is available or run inside a Kubernetes cluster."
                ) from exc

        self._apps_api = client.AppsV1Api()
        self._core_api = client.CoreV1Api()
        self._custom_api = client.CustomObjectsApi()

    def _ensure_namespace(self) -> None:
        core_api = self._core_api_client
        try:
            core_api.read_namespace(name=self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            core_api.create_namespace(
                body=client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=self.namespace)
                )
            )

    @staticmethod
    def _deployment_name(model_id: str) -> str:
        return f"model-{model_id}"

    @staticmethod
    def _service_name(model_id: str) -> str:
        return f"model-{model_id}-svc"

    def _build_deployment_body(
        self, deployment_name: str, spec: DeploymentSpec
    ) -> dict[str, Any]:
        labels = {
            "app": "ml-model",
            "model-id": spec.model_id,
            "managed-by": "mlapi-supervisor",
        }

        env_vars = [
            {"name": key, "value": value} for key, value in sorted(spec.env.items())
        ]
        pod_spec: dict[str, Any] = {
            "containers": [
                {
                    "name": "model",
                    "image": spec.image,
                    "ports": [{"containerPort": spec.container_port}],
                    "env": env_vars,
                }
            ]
        }
        if self.image_pull_secret_name:
            pod_spec["imagePullSecrets"] = [{"name": self.image_pull_secret_name}]

        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": deployment_name, "labels": labels},
            "spec": {
                "replicas": spec.replicas,
                "selector": {"matchLabels": {"model-id": spec.model_id}},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": pod_spec,
                },
            },
        }

    def _build_service_body(
        self, service_name: str, spec: DeploymentSpec
    ) -> dict[str, Any]:
        labels = {
            "app": "ml-model",
            "model-id": spec.model_id,
            "managed-by": "mlapi-supervisor",
        }

        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": service_name, "labels": labels},
            "spec": {
                "selector": {"model-id": spec.model_id},
                "ports": [
                    {
                        "name": "http",
                        "port": spec.service_port,
                        "targetPort": spec.container_port,
                    }
                ],
                "type": self.service_type,
            },
        }

    @staticmethod
    def _service_endpoints(service: client.V1Service | None) -> list[str]:
        if service is None:
            return []

        endpoints: list[str] = []
        status = service.status
        if status and status.load_balancer and status.load_balancer.ingress:
            for ingress in status.load_balancer.ingress:
                if ingress.ip:
                    endpoints.append(ingress.ip)
                if ingress.hostname:
                    endpoints.append(ingress.hostname)

        spec = service.spec
        if spec and spec.external_i_ps:
            endpoints.extend(spec.external_i_ps)

        if spec and spec.type == "NodePort" and spec.ports:
            for port in spec.ports:
                if port.node_port:
                    endpoints.append(str(port.node_port))

        deduplicated: list[str] = []
        for endpoint in endpoints:
            if endpoint not in deduplicated:
                deduplicated.append(endpoint)
        return deduplicated
