import asyncio
import logging

import docker
import docker.errors
from docker.types import DeviceRequest

from app.cluster.backend import BackendError, ModelSpec, RuntimeStatus
from app.config import Settings
from app.deployer.images import ImageInfo

logger = logging.getLogger(__name__)

MANAGED = {"mlapi.managed": "true"}


def _base(name: str) -> str:
    return f"mlapi-m-{name}"


class DockerBackend:
    """Runs models as containers on the local Docker engine (single-device installs).

    Every docker-py call is blocking, so the real work lives in synchronous helpers that run in a thread.
    """

    def __init__(self, settings: Settings, client=None):
        self._settings = settings
        self._client = client
        self._network = settings.docker_network

    @property
    def client(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    # ───────────── async interface ─────────────
    async def apply_model(self, spec: ModelSpec) -> None:
        await self._run(self._apply, spec)

    async def scale(self, name: str, replicas: int, role: str = "main") -> None:
        await self._run(self._scale, name, replicas, role)

    async def delete_model(self, name: str, *, keep_cache: bool = False) -> None:
        await self._run(self._delete, name, keep_cache)

    async def status(self, name: str) -> RuntimeStatus:
        return await self._run(self._status, name)

    async def endpoint(self, name: str) -> str:
        return await self._run(self._endpoint, name)

    async def list_models(self) -> list[str]:
        return await self._run(self._list)

    @staticmethod
    async def _run(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except BackendError:
            raise
        except docker.errors.DockerException as exc:
            raise BackendError(f"docker error: {exc}") from exc

    # ───────────── helpers (blocking) ─────────────
    def _get(self, container_name: str):
        try:
            return self.client.containers.get(container_name)
        except docker.errors.NotFound:
            return None

    def _ensure_network(self) -> None:
        try:
            self.client.networks.get(self._network)
        except docker.errors.NotFound:
            self.client.networks.create(self._network, driver="bridge", labels=MANAGED)

    def _ensure_volume(self, name: str, model: str) -> None:
        try:
            self.client.volumes.get(name)
        except docker.errors.NotFound:
            self.client.volumes.create(name=name, labels={**MANAGED, "mlapi.model": model})

    def _ensure_image(self, ref: str) -> None:
        try:
            self.client.images.get(ref)
            return
        except docker.errors.ImageNotFound:
            pass
        if ref.startswith("sha256:"):
            raise BackendError(f"local image {ref[:19]}... not found on this host")
        kwargs = {}
        if ref.startswith("ghcr.io/") and self._settings.ghcr_token:
            kwargs["auth_config"] = {"username": "token", "password": self._settings.ghcr_token}
        try:
            self.client.images.pull(ref, **kwargs)
        except docker.errors.DockerException as exc:
            raise BackendError(f"could not pull image: {exc}") from exc

    def _create(self, spec: ModelSpec, *, container: str, role: str, fastmlapi_role: str, resources, gpu: bool, cache: bool):
        env = dict(spec.env)
        env["FASTMLAPI_ROLE"] = fastmlapi_role  # platform variables always win over model-supplied ones
        if cache:
            env["FASTMLAPI_CACHE_DIR"] = "/cache"
            env["HF_HOME"] = "/cache/hf"
        else:
            env.pop("HF_HOME", None)
            env.pop("FASTMLAPI_CACHE_DIR", None)
        if spec.queue_url:
            env["FASTMLAPI_QUEUE_URL"] = spec.queue_url
        kwargs = dict(
            name=container,
            environment=env,
            labels={**MANAGED, "mlapi.model": spec.name, "mlapi.role": role},
            network=self._network,
            nano_cpus=resources.cpu_m * 1_000_000,
            mem_limit=resources.memory_bytes,
            memswap_limit=resources.memory_bytes,  # no swap: a limit is a limit
            restart_policy={"Name": "unless-stopped"},
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            pids_limit=1024,
        )
        if cache:
            kwargs["volumes"] = {f"{_base(spec.name)}-cache": {"bind": "/cache", "mode": "rw"}}
        if self._settings.docker_publish_ports and role == "main":
            kwargs["ports"] = {"8000/tcp": ("127.0.0.1", None)}  # random free port, localhost only
        if gpu:
            kwargs["device_requests"] = [DeviceRequest(count=1, capabilities=[["gpu"]])]
        if fastmlapi_role == "worker":
            kwargs["stop_timeout"] = spec.max_job_seconds
        return self.client.containers.create(spec.image, **kwargs)

    def _replace(self, container_name: str) -> bool:
        """Removes an existing container; returns whether it was running."""
        existing = self._get(container_name)
        if existing is None:
            return False
        was_running = existing.status == "running"
        existing.remove(force=True)
        return was_running

    def _apply(self, spec: ModelSpec) -> None:
        if spec.secret_refs:
            logger.warning("Docker backend has no secret store; secret_refs %s are ignored", list(spec.secret_refs))
        self._ensure_network()
        self._ensure_image(spec.image)
        self._ensure_volume(f"{_base(spec.name)}-cache", spec.name)
        base = _base(spec.name)
        if spec.mode == "queue":
            api_resources = spec.api_resources or spec.resources
            self._replace(base)  # a sync container of the same name would clash on re-registration
            self._replace(f"{base}-api")
            api = self._create(spec, container=f"{base}-api", role="main", fastmlapi_role="api",
                               resources=api_resources, gpu=False, cache=False)
            api.start()
            worker_was_running = self._replace(f"{base}-worker")
            worker = self._create(spec, container=f"{base}-worker", role="worker", fastmlapi_role="worker",
                                  resources=spec.resources, gpu=spec.resources.gpu, cache=True)
            if worker_was_running:
                worker.start()
        else:
            for stale in (f"{base}-api", f"{base}-worker"):
                self._replace(stale)
            self._replace(base)
            container = self._create(spec, container=base, role="main", fastmlapi_role="sync",
                                     resources=spec.resources, gpu=spec.resources.gpu, cache=True)
            container.start()

    def _main(self, name: str):
        return self._get(f"{_base(name)}-api") or self._get(_base(name))

    def _scale(self, name: str, replicas: int, role: str) -> None:
        container = self._get(f"{_base(name)}-worker") if role == "worker" else self._main(name)
        if container is None:
            raise BackendError(f"no {role} container for model {name!r}")
        if replicas <= 0:
            if container.status == "running":
                container.stop()
        elif container.status != "running":
            container.start()

    def _delete(self, name: str, keep_cache: bool) -> None:
        for container in (_base(name), f"{_base(name)}-api", f"{_base(name)}-worker"):
            existing = self._get(container)
            if existing is not None:
                existing.remove(force=True)
        if not keep_cache:
            try:
                self.client.volumes.get(f"{_base(name)}-cache").remove(force=True)
            except docker.errors.NotFound:
                pass

    @staticmethod
    def _running(container) -> bool:
        state = container.attrs.get("State", {})
        return container.status == "running" and not state.get("Restarting", False)

    def _status(self, name: str) -> RuntimeStatus:
        main = self._main(name)
        if main is None:
            return RuntimeStatus(exists=False)
        main.reload()
        worker = self._get(f"{_base(name)}-worker")
        if worker is not None:
            worker.reload()
        running = self._running(main)
        return RuntimeStatus(
            exists=True,
            ready=running,
            replicas=1 if main.status == "running" else 0,
            worker_replicas=1 if worker is not None and worker.status == "running" else 0,
            restarts=int(main.attrs.get("RestartCount", 0)),
        )

    def _endpoint(self, name: str) -> str:
        main = self._main(name)
        if self._settings.docker_publish_ports and main is not None:
            main.reload()
            bindings = (main.attrs.get("NetworkSettings", {}).get("Ports") or {}).get("8000/tcp")
            if bindings:
                return f"http://127.0.0.1:{bindings[0]['HostPort']}"
        suffix = "-api" if self._get(f"{_base(name)}-api") is not None else ""
        return f"http://{_base(name)}{suffix}:8000"

    def _list(self) -> list[str]:
        containers = self.client.containers.list(all=True, filters={"label": ["mlapi.managed=true"]})
        return sorted({c.labels["mlapi.model"] for c in containers if "mlapi.model" in c.labels})


class DockerInspector:
    """Reads labels and the image id of an image that exists on the local Docker engine."""

    def __init__(self, client):
        self._client = client

    async def inspect(self, image: str, source: str) -> ImageInfo:
        if source != "local":
            raise LookupError(image)

        def _inspect():
            try:
                found = self._client.images.get(image)
            except docker.errors.ImageNotFound:
                raise LookupError(image)
            package = image.rsplit("/", 1)[-1].split("@")[0].split(":")[0]
            return ImageInfo(labels=dict(found.labels or {}), digest=found.id, package_name=package)

        return await asyncio.to_thread(_inspect)
