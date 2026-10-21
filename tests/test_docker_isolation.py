import pytest

from app.cluster.backend import ModelSpec
from app.cluster.docker import DockerBackend
from app.config import Settings
from app.registry.resources import Resources
from tests.fake_docker import FakeDockerClient, FakeImage

RES = Resources(cpu_m=500, memory_bytes=1024**3, gpu=False, disk_bytes=0)


@pytest.fixture
def client():
    c = FakeDockerClient()
    c.local_images["img:1"] = FakeImage("img:1", {}, "sha256:x")
    for peer in ("mlapi-router", "mlapi-supervisor"):  # the platform's own containers
        c.containers.create("platform:1", name=peer)
    return c


def backend(client, **settings):
    base = dict(_env_file=None, docker_network="mlapi-models", docker_isolate_models=True,
                docker_peers="mlapi-router, mlapi-supervisor")
    return DockerBackend(Settings(**{**base, **settings}), client=client)


def spec(name="ecg", mode="sync", **kw):
    return ModelSpec(name=name, image="img:1", mode=mode, resources=RES, api_resources=RES,
                     queue_url="redis://q:6379/0" if mode == "queue" else None, **kw)


async def test_by_default_models_share_one_network(client):
    b = DockerBackend(Settings(_env_file=None, docker_network="mlapi-models"), client=client)
    await b.apply_model(spec())
    assert client.container_map["mlapi-m-ecg"].kwargs["network"] == "mlapi-models"
    assert "mlapi-net-ecg" not in client.network_map


async def test_an_isolated_model_gets_its_own_network_with_the_platform_attached(client):
    await backend(client).apply_model(spec())
    assert client.container_map["mlapi-m-ecg"].kwargs["network"] == "mlapi-net-ecg"
    net = client.network_map["mlapi-net-ecg"]
    assert net["labels"]["mlapi.managed"] == "true" and net["labels"]["mlapi.model"] == "ecg"
    assert net.members == {"mlapi-router", "mlapi-supervisor"}


async def test_models_do_not_share_a_network(client):
    b = backend(client)
    await b.apply_model(spec("ecg"))
    await b.apply_model(spec("xray"))
    assert client.container_map["mlapi-m-ecg"].kwargs["network"] != client.container_map["mlapi-m-xray"].kwargs["network"]
    assert "mlapi-m-xray" not in client.network_map["mlapi-net-ecg"].members


async def test_applying_again_reuses_the_network_without_error(client):
    b = backend(client)
    await b.apply_model(spec())
    await b.apply_model(spec())
    assert client.network_map["mlapi-net-ecg"].members == {"mlapi-router", "mlapi-supervisor"}


async def test_a_missing_peer_is_skipped_not_fatal(client):
    client.container_map.pop("mlapi-router")  # e.g. the Router runs on the host, not in Docker
    await backend(client).apply_model(spec())
    assert client.network_map["mlapi-net-ecg"].members == {"mlapi-supervisor"}


async def test_queue_models_also_join_the_network_the_queue_redis_is_on(client):
    b = backend(client)
    await b.apply_model(spec(mode="queue"))
    shared = client.network_map["mlapi-models"]
    assert {"mlapi-m-ecg-api", "mlapi-m-ecg-worker"} <= shared.members
    await b.apply_model(spec("xray"))  # a sync model stays off it
    assert "mlapi-m-xray" not in shared.members


async def test_the_router_still_finds_the_model_by_name(client):
    b = backend(client)
    await b.apply_model(spec())
    assert await b.endpoint("ecg") == "http://mlapi-m-ecg:8000"


async def test_removing_a_model_removes_its_network(client):
    b = backend(client)
    await b.apply_model(spec())
    await b.delete_model("ecg")
    assert "mlapi-net-ecg" not in client.network_map
    assert client.container_map.keys() == {"mlapi-router", "mlapi-supervisor"}  # the platform's own containers stay
