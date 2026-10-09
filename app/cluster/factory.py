from app.cluster.backend import ClusterBackend
from app.config import Settings


def make_backend(settings: Settings) -> ClusterBackend:
    name = settings.cluster_backend
    if name == "fake":
        from app.cluster.fake import FakeBackend

        return FakeBackend()
    if name == "docker":
        from app.cluster.docker import DockerBackend

        return DockerBackend(settings)
    if name == "kubernetes":
        from app.cluster.kubernetes import KubernetesBackend

        return KubernetesBackend(settings)
    raise ValueError(f"unknown CLUSTER_BACKEND: {name!r}")
