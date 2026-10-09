import pytest

from app.events import EventBus
from app.registry import states
from app.registry.errors import Conflict, Invalid, NotFound
from app.registry.approval import Decision, decide
from app.registry.labels import ModelLabels
from app.registry.queries import get_model_by_name, get_request, list_requests
from app.registry.resources import Resources
from app.registry.service import RegistryService

GI = 1024**3


def R(cpu=1000, mem=2 * GI, gpu=False, disk=5 * GI):
    return Resources(cpu_m=cpu, memory_bytes=mem, gpu=gpu, disk_bytes=disk)


def L(resources=None, mode="sync", name="ecg"):
    return ModelLabels(name=name, mode=mode, resources=resources or R())


class Notifier:
    def __init__(self):
        self.events = []

    async def notify(self, session, event, payload):
        self.events.append((event, payload["model"], payload["digest"]))


@pytest.fixture
def notifier():
    return Notifier()


@pytest.fixture
def service(sessionmaker, notifier):
    return RegistryService(EventBus(sessionmaker, notifier))


async def submit(service, session, digest="sha256:a", labels=None, source="ghcr"):
    result = await service.submit_digest(
        session, name="ecg", image="ghcr.io/org/ecg", digest=digest, labels=labels or L(), source=source
    )
    await session.commit()
    return result


def test_decide():
    assert decide(None, R()) is Decision.NEEDS_APPROVAL
    assert decide(R(), R()) is Decision.AUTO
    assert decide(R(), R(cpu=500)) is Decision.AUTO
    assert decide(R(), R(gpu=True)) is Decision.NEEDS_APPROVAL


async def test_first_digest_creates_model_pending_approval(service, session, notifier):
    result = await submit(service, session)
    model = await get_model_by_name(session, "ecg")
    assert result.action == "approval"
    assert model.state == states.PENDING_APPROVAL
    assert model.pending_digest == "sha256:a" and model.approved_resources is None
    requests = await list_requests(session, status=states.REQ_PENDING)
    assert len(requests) == 1 and requests[0].requested == R().to_dict()
    await service._events.drain()
    assert ("approval.requested", "ecg", "sha256:a") in notifier.events


async def test_approve_sets_resources_and_returns_digest_to_deploy(service, session, notifier):
    await submit(service, session)
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    model, digest = await service.approve(session, req.id)
    await session.commit()
    assert digest == "sha256:a"
    assert model.approved_resources == R().to_dict()
    assert (await get_request(session, req.id)).status == states.REQ_APPROVED
    await service._events.drain()
    assert ("approval.approved", "ecg", "sha256:a") in notifier.events


async def test_approve_with_lower_values(service, session):
    await submit(service, session, labels=L(R(cpu=4000, mem=8 * GI, gpu=True)))
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    model, _ = await service.approve(session, req.id, override=R(cpu=2000, mem=4 * GI, gpu=False))
    assert model.approved_resources == R(cpu=2000, mem=4 * GI, gpu=False).to_dict()


@pytest.mark.parametrize(
    "override",
    [R(cpu=4001), R(mem=2 * GI + 1), R(disk=5 * GI + 1), R(gpu=True)],
)
async def test_approve_never_above_requested(service, session, override):
    await submit(service, session, labels=L(R(cpu=4000, mem=2 * GI)))
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    with pytest.raises(Invalid):
        await service.approve(session, req.id, override=override)
    assert (await get_request(session, req.id)).status == states.REQ_PENDING


async def test_double_decision_and_unknown_request(service, session):
    await submit(service, session)
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    await service.approve(session, req.id)
    with pytest.raises(Conflict):
        await service.approve(session, req.id)
    with pytest.raises(Conflict):
        await service.reject(session, req.id)
    with pytest.raises(NotFound):
        await service.approve(session, 9999)


async def _running_model(service, session, resources=None):
    """A model approved and 'deployed' with sha256:a."""
    await submit(service, session, labels=L(resources or R()))
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    model, _ = await service.approve(session, req.id)
    model.current_digest, model.pending_digest, model.state = "sha256:a", None, states.READY
    await session.commit()
    return model


async def test_new_digest_with_same_resources_deploys_automatically(service, session):
    await _running_model(service, session)
    result = await submit(service, session, digest="sha256:b")
    assert result.action == "deploy" and result.digest == "sha256:b"
    assert await list_requests(session, status=states.REQ_PENDING) == []


async def test_new_digest_with_lower_resources_deploys_without_shrinking_approval(service, session):
    model = await _running_model(service, session)
    result = await submit(service, session, digest="sha256:b", labels=L(R(cpu=500)))
    assert result.action == "deploy"
    assert (await get_model_by_name(session, "ecg")).approved_resources == R().to_dict()


@pytest.mark.parametrize("labels", [L(R(cpu=2000)), L(R(mem=4 * GI)), L(R(disk=10 * GI)), L(R(gpu=True))])
async def test_any_increase_needs_approval_and_keeps_old_version_running(service, session, labels):
    await _running_model(service, session)
    result = await submit(service, session, digest="sha256:b", labels=labels)
    model = await get_model_by_name(session, "ecg")
    assert result.action == "approval"
    assert model.state == states.READY and model.current_digest == "sha256:a"
    assert model.pending_digest == "sha256:b"
    assert model.approved_resources == R().to_dict()


async def test_newer_pending_digest_supersedes_older_request(service, session):
    await _running_model(service, session)
    await submit(service, session, digest="sha256:b", labels=L(R(cpu=2000)))
    await submit(service, session, digest="sha256:c", labels=L(R(cpu=3000)))
    statuses = sorted(r.status for r in await list_requests(session))
    assert statuses.count(states.REQ_SUPERSEDED) == 1
    assert [r.digest for r in await list_requests(session, status=states.REQ_PENDING)] == ["sha256:c"]
    assert (await get_model_by_name(session, "ecg")).pending_digest == "sha256:c"


async def test_same_digest_again_is_noop(service, session):
    await _running_model(service, session)
    assert (await submit(service, session, digest="sha256:a")).action == "noop"
    await submit(service, session, digest="sha256:b", labels=L(R(cpu=2000)))
    assert (await submit(service, session, digest="sha256:b", labels=L(R(cpu=2000)))).action == "noop"
    assert len(await list_requests(session, status=states.REQ_PENDING)) == 1


async def test_failed_digest_is_not_resubmitted(service, session):
    model = await _running_model(service, session)
    model.failed_digest = "sha256:bad"
    await session.commit()
    assert (await submit(service, session, digest="sha256:bad")).action == "noop"


async def test_reject_first_time_model_marks_failed(service, session, notifier):
    await submit(service, session)
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    model = await service.reject(session, req.id, note="too big")
    await session.commit()
    assert model.state == states.FAILED and "too big" in model.state_detail
    assert model.pending_digest is None and model.failed_digest is None     # a rejection is not a failed deploy
    assert (await submit(service, session)).action == "noop"   # same digest is not re-requested
    await service._events.drain()
    assert ("approval.rejected", "ecg", "sha256:a") in notifier.events


async def test_reject_keeps_running_version(service, session):
    await _running_model(service, session)
    await submit(service, session, digest="sha256:b", labels=L(R(cpu=2000)))
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    model = await service.reject(session, req.id)
    assert model.state == states.READY and model.current_digest == "sha256:a"
    assert model.pending_digest is None and model.failed_digest is None


async def test_mode_change_is_refused(service, session):
    await _running_model(service, session)
    with pytest.raises(Conflict):
        await submit(service, session, digest="sha256:b", labels=L(mode="queue"))


async def test_removed_model_can_be_reregistered_from_scratch(service, session):
    model = await _running_model(service, session)
    model.state, model.current_digest = states.REMOVED, None
    await session.commit()
    result = await submit(service, session, digest="sha256:z")
    model = await get_model_by_name(session, "ecg")
    assert result.action == "approval"
    assert model.state == states.PENDING_APPROVAL and model.approved_resources is None


# ───────────── review findings ─────────────
async def test_an_auto_deploy_supersedes_older_pending_requests(service, session):
    """A running; B asks for more (pending); C fits the approval and deploys. Approving B later must not roll back to B."""
    await _running_model(service, session)
    await submit(service, session, digest="sha256:b", labels=L(R(cpu=4000)))
    req_b = (await list_requests(session, status=states.REQ_PENDING))[0]
    result = await submit(service, session, digest="sha256:c")
    assert result.action == "deploy"
    model = await get_model_by_name(session, "ecg")
    assert model.pending_digest is None
    assert (await get_request(session, req_b.id)).status == states.REQ_SUPERSEDED
    with pytest.raises(Conflict):
        await service.approve(session, req_b.id)


async def test_a_rejected_digest_is_never_resubmitted_even_after_a_new_one_came_and_went(service, session):
    await submit(service, session)
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    await service.reject(session, req.id)
    await session.commit()
    await submit(service, session, digest="sha256:other", labels=L(R(cpu=500)))
    assert (await submit(service, session, digest="sha256:a")).action == "noop"


async def test_rejecting_a_request_does_not_fail_a_model_that_is_deploying(service, session):
    await submit(service, session)
    req = (await list_requests(session, status=states.REQ_PENDING))[0]
    model, _ = await service.approve(session, req.id)
    await submit(service, session, digest="sha256:b", labels=L(R(cpu=9000)))
    req_b = (await list_requests(session, status=states.REQ_PENDING))[0]
    model.state = states.DEPLOYING                      # the first deploy is running right now
    await session.commit()
    await service.reject(session, req_b.id)
    assert (await get_model_by_name(session, "ecg")).state == states.DEPLOYING


async def test_another_package_cannot_claim_an_existing_models_name(service, session):
    await _running_model(service, session)
    with pytest.raises(Conflict, match="belongs to"):
        await service.submit_digest(session, name="ecg", image="ghcr.io/org/evil-package", digest="sha256:evil",
                                    labels=L(R(cpu=100)), source="ghcr")
    model = await get_model_by_name(session, "ecg")
    assert model.image == "ghcr.io/org/ecg" and model.pending_digest is None
    assert await list_requests(session, status=states.REQ_PENDING) == []


async def test_a_model_cannot_switch_between_ghcr_and_local_sources(service, session):
    await _running_model(service, session)
    with pytest.raises(Conflict):
        await service.submit_digest(session, name="ecg", image="ecg-local:1", digest="sha256:l", labels=L(), source="local")


async def test_a_local_model_may_change_its_tag(service, session):
    await service.submit_digest(session, name="ecg", image="ecg-local:1", digest="sha256:l1", labels=L(), source="local")
    result = await service.submit_digest(session, name="ecg", image="ecg-local:2", digest="sha256:l2", labels=L(), source="local")
    assert result.action == "approval"
    assert (await get_model_by_name(session, "ecg")).image == "ecg-local:2"
