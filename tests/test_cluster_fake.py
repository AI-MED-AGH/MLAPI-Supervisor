import pytest

from app.cluster.backend import BackendError, ModelSpec
from app.cluster.fake import FakeBackend
from app.registry.resources import Resources

GI = 1024**3
RES = Resources(cpu_m=1000, memory_bytes=GI, gpu=False, disk_bytes=GI)
GPU = Resources(cpu_m=1000, memory_bytes=GI, gpu=True, disk_bytes=GI)


def spec(name="m1", image="repo/m1@sha256:aaa", mode="sync", resources=RES):
    return ModelSpec(name=name, image=image, mode=mode, resources=resources)


async def test_unknown_model_does_not_exist():
    status = await FakeBackend().status("nope")
    assert status.exists is False and status.ready is False


async def test_apply_makes_model_ready_immediately_by_default():
    b = FakeBackend()
    await b.apply_model(spec())
    status = await b.status("m1")
    assert status.exists and status.ready and status.replicas == 1
    assert await b.list_models() == ["m1"]


async def test_ready_after_n_polls():
    b = FakeBackend(ready_after=2)
    await b.apply_model(spec())
    assert [(await b.status("m1")).ready for _ in range(3)] == [False, False, True]


async def test_scale_to_zero_and_back():
    b = FakeBackend()
    await b.apply_model(spec())
    await b.scale("m1", 0)
    s = await b.status("m1")
    assert s.replicas == 0 and s.ready is False
    await b.scale("m1", 1)
    assert (await b.status("m1")).ready


async def test_queue_mode_tracks_api_and_worker_separately():
    b = FakeBackend()
    await b.apply_model(spec(mode="queue"))
    s = await b.status("m1")
    assert s.ready and s.replicas == 1 and s.worker_replicas == 0
    await b.scale("m1", 1, role="worker")
    assert (await b.status("m1")).worker_replicas == 1


async def test_unschedulable_gpu_reports_pending_reason():
    b = FakeBackend(gpu_slots=0)
    await b.apply_model(spec(resources=GPU))
    s = await b.status("m1")
    assert s.ready is False and "gpu" in s.pending_reason.lower()
    b.gpu_slots = 1
    assert (await b.status("m1")).ready


async def test_apply_failure_is_raised_and_recorded():
    b = FakeBackend()
    b.fail_apply = BackendError("registry unreachable")
    with pytest.raises(BackendError):
        await b.apply_model(spec())
    assert await b.list_models() == []


async def test_apply_replaces_spec_for_new_digest():
    b = FakeBackend()
    await b.apply_model(spec(image="r/m1@sha256:aaa"))
    await b.apply_model(spec(image="r/m1@sha256:bbb"))
    assert b.models["m1"].spec.image == "r/m1@sha256:bbb"


async def test_delete_and_scale_unknown():
    b = FakeBackend()
    await b.apply_model(spec())
    await b.delete_model("m1")
    assert (await b.status("m1")).exists is False
    with pytest.raises(BackendError):
        await b.scale("m1", 1)


async def test_endpoint_and_call_log():
    b = FakeBackend()
    await b.apply_model(spec())
    assert await b.endpoint("m1") == "http://fake-m1:8000"
    assert b.calls[0][0] == "apply_model"
