"""A small in-memory stand-in for the parts of docker-py that DockerBackend uses."""
import builtins

import docker.errors


class FakeContainer:
    def __init__(self, client, name, image, kwargs):
        self._client = client
        self.name = name
        self.image = image
        self.kwargs = kwargs
        self.labels = dict(kwargs.get("labels", {}))
        self.status = "created"
        self.restart_count = 0
        self.restarting = False
        self.exit_code = 0

    @property
    def attrs(self):
        ports = {}
        if "ports" in self.kwargs:
            ports = {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32768"}]}
        return {"RestartCount": self.restart_count,
                "State": {"Status": self.status, "Restarting": self.restarting, "ExitCode": self.exit_code},
                "NetworkSettings": {"Ports": ports}}

    def start(self):
        self.status = "running"

    def stop(self, timeout=10):
        self.status = "exited"

    def remove(self, force=False):
        self._client.container_map.pop(self.name, None)

    def reload(self):
        pass


class _Containers:
    def __init__(self, client):
        self._c = client

    def create(self, image, **kwargs):
        name = kwargs["name"]
        if name in self._c.container_map:
            raise docker.errors.APIError("Conflict. The container name is already in use")
        c = FakeContainer(self._c, name, image, kwargs)
        self._c.container_map[name] = c
        return c

    def get(self, name):
        try:
            return self._c.container_map[name]
        except KeyError:
            raise docker.errors.NotFound("no such container")

    def list(self, all=False, filters=None):
        wanted = (filters or {}).get("label", [])
        out = []
        for c in self._c.container_map.values():
            if all or c.status == "running":
                if builtins.all(self._matches(c, w) for w in wanted):
                    out.append(c)
        return out

    @staticmethod
    def _matches(container, wanted):
        key, _, value = wanted.partition("=")
        return container.labels.get(key) == value if value else key in container.labels


class _Images:
    def __init__(self, client):
        self._c = client

    def get(self, ref):
        if ref not in self._c.local_images:
            raise docker.errors.ImageNotFound("no such image")
        return self._c.local_images[ref]

    def pull(self, repository, tag=None):
        self._c.pulled.append(repository if tag is None else f"{repository}:{tag}")
        if repository in self._c.unpullable:
            raise docker.errors.ImageNotFound("pull access denied")
        self._c.local_images[repository] = FakeImage(repository, {}, "sha256:pulled")


class FakeImage:
    def __init__(self, ref, labels, image_id):
        self.tags, self.labels, self.id = [ref], labels, image_id
        self.attrs = {"Config": {"Labels": labels}, "RepoDigests": []}


class _Volumes:
    def __init__(self, client):
        self._c = client

    def create(self, name=None, labels=None):
        self._c.volume_map[name] = {"labels": labels or {}}
        return FakeVolume(self._c, name)

    def get(self, name):
        if name not in self._c.volume_map:
            raise docker.errors.NotFound("no such volume")
        return FakeVolume(self._c, name)


class FakeVolume:
    def __init__(self, client, name):
        self._c, self.name = client, name

    def remove(self, force=False):
        self._c.volume_map.pop(self.name, None)


class _Networks:
    def __init__(self, client):
        self._c = client

    def get(self, name):
        if name not in self._c.network_map:
            raise docker.errors.NotFound("no such network")
        return self._c.network_map[name]

    def create(self, name, driver="bridge", labels=None):
        self._c.network_map[name] = {"driver": driver, "labels": labels or {}}
        return self._c.network_map[name]


class FakeDockerClient:
    def __init__(self):
        self.container_map: dict[str, FakeContainer] = {}
        self.volume_map: dict[str, dict] = {}
        self.network_map: dict[str, dict] = {}
        self.local_images: dict[str, FakeImage] = {}
        self.pulled: list[str] = []
        self.unpullable: set[str] = set()
        self.containers = _Containers(self)
        self.images = _Images(self)
        self.volumes = _Volumes(self)
        self.networks = _Networks(self)
