import pytest

from app.poller.ghcr import GhcrClient, GhcrError, RateLimited
from app.poller.service import Poller
from app.registry import states
from app.registry.queries import list_requests
from tests.fake_ghcr import FakeGhcr
from tests.support import get

MODEL = {"mlapi.model": "true", "mlapi.model.name": "ecg", "mlapi.cpu": "1", "mlapi.memory": "2Gi",
         "mlapi.gpu": "false", "mlapi.disk": "5Gi"}


@pytest.fixture
def ghcr():
    return FakeGhcr()


def make_client(ghcr):
    return GhcrClient(token=ghcr.token, org=ghcr.org, http=ghcr.client())


def make_poller(env, ghcr):
    return Poller(client=make_client(ghcr), sessionmaker=env.sm, registry=env.registry,
                  deployer=env.deployer, org=ghcr.org, events=env.events)


def count(ghcr, needle):
    return sum(1 for _, p in ghcr.requests if needle in p)


# ───────────── client ─────────────
async def test_list_packages_follows_pagination(ghcr):
    for i in range(5):
        ghcr.add(f"pkg{i}", MODEL)
    ghcr.page_size = 2
    assert await make_client(ghcr).list_packages() == [f"pkg{i}" for i in range(5)]


async def test_resolve_digest_and_labels(ghcr):
    entry = ghcr.add("ecg", MODEL)
    client = make_client(ghcr)
    digest = await client.resolve_digest("ecg")
    assert digest == entry["manifest_digest"]
    assert await client.read_labels("ecg", digest) == MODEL


async def test_multiarch_index_uses_the_amd64_image(ghcr):
    ghcr.add("ecg", MODEL, multiarch=True)
    client = make_client(ghcr)
    digest = await client.resolve_digest("ecg")
    assert digest == ghcr.top_digest("ecg")
    assert (await client.read_labels("ecg", digest))["mlapi.model"] == "true"   # not the arm64 variant


async def test_unknown_package_has_no_digest(ghcr):
    assert await make_client(ghcr).resolve_digest("nope") is None


async def test_rate_limit_and_server_errors_are_distinct(ghcr):
    ghcr.add("ecg", MODEL)
    ghcr.fail["/orgs/"] = 429
    with pytest.raises(RateLimited):
        await make_client(ghcr).list_packages()
    ghcr.fail.clear()
    ghcr.fail["/manifests/"] = 500
    with pytest.raises(GhcrError):
        await make_client(ghcr).resolve_digest("ecg")


# ───────────── poller ─────────────
async def test_new_model_image_creates_a_pending_approval_without_deploying(env, ghcr):
    ghcr.add("ecg", MODEL)
    await make_poller(env, ghcr).tick()
    model = await get(env)
    assert model.state == states.PENDING_APPROVAL and model.image == "ghcr.io/org/ecg"
    assert model.source == "ghcr" and model.pending_digest == ghcr.top_digest("ecg")
    async with env.sm() as s:
        assert len(await list_requests(s, status=states.REQ_PENDING)) == 1
    assert env.backend.models == {}


async def test_unchanged_digest_is_not_read_again(env, ghcr):
    ghcr.add("ecg", MODEL)
    poller = make_poller(env, ghcr)
    await poller.tick()
    blobs = count(ghcr, "/blobs/")
    await poller.tick()
    assert count(ghcr, "/blobs/") == blobs


async def test_non_model_images_are_ignored_and_remembered(env, ghcr):
    ghcr.add("website", {"org.opencontainers.image.title": "site"})
    poller = make_poller(env, ghcr)
    await poller.tick()
    await poller.tick()
    assert count(ghcr, "/blobs/") == 1
    assert await get(env, "website") is None


async def test_approved_model_with_new_digest_deploys_automatically(env, ghcr):
    from tests.support import make_model

    entry = ghcr.add("ecg", MODEL)
    poller = make_poller(env, ghcr)
    await poller.tick()
    async with env.sm() as s:                       # an admin approves the first request and it deploys
        req = (await list_requests(s, status=states.REQ_PENDING))[0]
        model, digest = await env.registry.approve(s, req.id)
        await env.deployer.begin(s, model, digest)
        await s.commit()
    await env.deployer.run(model.id, digest)

    ghcr.add("ecg", {**MODEL, "mlapi.cpu": "500m"})   # lower request: no approval needed
    await poller.tick()
    await env.deployer.drain()
    model = await get(env)
    assert model.current_digest == ghcr.top_digest("ecg") and model.previous_digest == entry["manifest_digest"]
    assert model.state == states.READY


async def test_resource_increase_waits_for_approval(env, ghcr):
    ghcr.add("ecg", MODEL)
    poller = make_poller(env, ghcr)
    await poller.tick()
    async with env.sm() as s:
        req = (await list_requests(s, status=states.REQ_PENDING))[0]
        model, digest = await env.registry.approve(s, req.id)
        await env.deployer.begin(s, model, digest)
        await s.commit()
    await env.deployer.run(model.id, digest)
    first = (await get(env)).current_digest

    ghcr.add("ecg", {**MODEL, "mlapi.cpu": "4"})
    await poller.tick()
    await env.deployer.drain()
    model = await get(env)
    assert model.current_digest == first and model.state == states.READY
    assert model.pending_digest == ghcr.top_digest("ecg")
    async with env.sm() as s:
        assert len(await list_requests(s, status=states.REQ_PENDING)) == 1


async def test_malformed_labels_emit_a_failure_event_and_do_not_stop_the_poll(env, ghcr):
    ghcr.add("bad", {**MODEL, "mlapi.model.name": "bad", "mlapi.cpu": "lots"})
    ghcr.add("good", {**MODEL, "mlapi.model.name": "good"})
    poller = make_poller(env, ghcr)
    await poller.tick()
    await env.events.drain()
    assert await get(env, "bad") is None
    assert (await get(env, "good")).state == states.PENDING_APPROVAL
    failed = [e for e in env.notifier.events if e[0] == "deploy.failed"]
    assert failed and failed[0][1] == "bad" and "cpu" in failed[0][3]["reason"]
    await poller.tick()                               # a bad digest is not reported over and over
    await env.events.drain()
    assert len([e for e in env.notifier.events if e[0] == "deploy.failed"]) == 1


async def test_failure_on_one_package_is_retried_next_tick(env, ghcr):
    ghcr.add("one", {**MODEL, "mlapi.model.name": "one"})
    ghcr.add("two", {**MODEL, "mlapi.model.name": "two"})
    poller = make_poller(env, ghcr)
    ghcr.fail["/one/manifests/"] = 500
    await poller.tick()
    assert await get(env, "one") is None and await get(env, "two") is not None
    ghcr.fail.clear()
    await poller.tick()
    assert await get(env, "one") is not None


async def test_rate_limit_ends_the_tick_quietly(env, ghcr):
    ghcr.add("ecg", MODEL)
    poller = make_poller(env, ghcr)
    ghcr.fail["/manifests/"] = 429
    await poller.tick()                                # must not raise
    assert await get(env) is None
    ghcr.fail.clear()
    await poller.tick()
    assert await get(env) is not None


async def test_mode_change_is_reported_not_applied(env, ghcr):
    ghcr.add("ecg", MODEL)
    poller = make_poller(env, ghcr)
    await poller.tick()
    ghcr.add("ecg", {**MODEL, "mlapi.mode": "queue"})
    await poller.tick()
    await env.events.drain()
    assert (await get(env)).mode == "sync"
    assert any(e[0] == "deploy.failed" and "mode" in e[3]["reason"] for e in env.notifier.events)


async def test_list_failure_is_swallowed(env, ghcr):
    ghcr.fail["/orgs/"] = 500
    await make_poller(env, ghcr).tick()
