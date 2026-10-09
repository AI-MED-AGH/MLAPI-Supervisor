import json

import pytest

from tests.conftest import ADMIN_HEADERS as H

LABELS = {"mlapi.model": "true", "mlapi.model.name": "ecg", "mlapi.cpu": "1", "mlapi.memory": "2Gi",
          "mlapi.gpu": "false", "mlapi.disk": "5Gi"}
IMAGE = "ecg-local:latest"


async def drain(api):
    if api.app.state.services:
        await api.app.state.services.deployer.drain()
        await api.app.state.services.events.drain()


async def register(api, labels=None, digest="sha256:aaa", image=IMAGE, **body):
    api.inspector.add(image, labels or LABELS, digest=digest)
    return await api.post("/v1/models", headers=H, json={"image": image, "source": "local", **body})


async def approve_first(api):
    pending = (await api.get("/v1/approvals", headers=H)).json()
    r = await api.post(f"/v1/approvals/{pending[0]['id']}/approve", headers=H)
    await drain(api)
    return r


async def deployed(api):
    await register(api)
    await approve_first(api)


@pytest.mark.parametrize(
    "method,path",
    [("get", "/v1/models"), ("post", "/v1/models"), ("get", "/v1/models/x"), ("patch", "/v1/models/x/config"),
     ("post", "/v1/models/x/redeploy"), ("post", "/v1/models/x/rollback"), ("post", "/v1/models/x/sleep"),
     ("post", "/v1/models/x/wake"), ("delete", "/v1/models/x"), ("get", "/v1/approvals"),
     ("post", "/v1/approvals/1/approve"), ("post", "/v1/approvals/1/reject")],
)
async def test_every_endpoint_requires_admin(api, method, path):
    assert (await getattr(api, method)(path)).status_code == 401


async def test_register_local_image_waits_for_approval(api):
    r = await register(api)
    assert r.status_code == 202
    assert r.json()["action"] == "approval"
    assert r.json()["model"]["state"] == "pending_approval"
    approvals = (await api.get("/v1/approvals", headers=H)).json()
    assert len(approvals) == 1 and approvals[0]["model"] == "ecg" and approvals[0]["currently_approved"] is None
    assert approvals[0]["requested"] == {"cpu_m": 1000, "memory_bytes": 2 * 1024**3, "gpu": False, "disk_bytes": 5 * 1024**3}
    assert api.backend.models == {}                       # nothing is started before approval


async def test_approval_starts_deploy_and_model_becomes_ready(api, redis):
    await register(api)
    r = await approve_first(api)
    assert r.status_code == 200 and r.json()["deploy_started"] is True
    detail = (await api.get("/v1/models/ecg", headers=H)).json()
    assert detail["state"] == "ready" and detail["current_digest"] == "sha256:aaa"
    assert detail["deployments"][0]["status"] == "succeeded"
    assert detail["schema"]["model_version"] == "1.0.0"
    assert detail["runtime"]["ready"] is True
    assert json.loads(await redis.get("route:ecg"))["state"] == "ready"
    assert (await api.get("/v1/models", headers=H)).json()[0]["name"] == "ecg"


async def test_approval_override_cannot_exceed_request(api):
    await register(api)
    pending = (await api.get("/v1/approvals", headers=H)).json()[0]
    r = await api.post(f"/v1/approvals/{pending['id']}/approve", headers=H, json={"cpu": "4"})
    assert r.status_code == 422
    r = await api.post(f"/v1/approvals/{pending['id']}/approve", headers=H, json={"cpu": "lots"})
    assert r.status_code == 422
    r = await api.post(f"/v1/approvals/{pending['id']}/approve", headers=H, json={"cpu": "500m", "memory": "1Gi"})
    assert r.status_code == 200
    await drain(api)
    assert (await api.get("/v1/models/ecg", headers=H)).json()["approved_resources"]["cpu_m"] == 500


async def test_reject_and_unknown_and_repeat(api):
    await register(api)
    pending = (await api.get("/v1/approvals", headers=H)).json()[0]
    r = await api.post(f"/v1/approvals/{pending['id']}/reject", headers=H, json={"note": "too big"})
    assert r.status_code == 200 and r.json()["state"] == "failed"
    assert (await api.post(f"/v1/approvals/{pending['id']}/reject", headers=H)).status_code == 409
    assert (await api.post("/v1/approvals/999/approve", headers=H)).status_code == 404
    assert (await api.get("/v1/approvals?status_filter=all", headers=H)).json()[0]["status"] == "rejected"


async def test_register_errors(api):
    r = await api.post("/v1/models", headers=H, json={"image": "missing:1", "source": "local"})
    assert r.status_code == 404
    api.inspector.add("plain:1", {})
    r = await api.post("/v1/models", headers=H, json={"image": "plain:1"})
    assert r.status_code == 422 and "mlapi.model" in r.json()["detail"]
    r = await register(api, labels={**LABELS, "mlapi.cpu": "lots"}, image="bad:1")
    assert r.status_code == 422
    assert (await api.post("/v1/models", headers=H, json={"image": ""})).status_code == 422
    assert (await api.post("/v1/models", headers=H, json={"image": "x", "source": "evil"})).status_code == 422


async def test_unknown_model_is_404(api):
    for method, path in [("get", "/v1/models/nope"), ("post", "/v1/models/nope/sleep"),
                         ("post", "/v1/models/nope/redeploy"), ("delete", "/v1/models/nope")]:
        assert (await getattr(api, method)(path, headers=H)).status_code == 404


async def test_registering_a_new_digest_with_same_resources_redeploys(api):
    await deployed(api)
    r = await register(api, digest="sha256:bbb")
    assert r.json()["action"] == "deploy"
    await drain(api)
    detail = (await api.get("/v1/models/ecg", headers=H)).json()
    assert detail["current_digest"] == "sha256:bbb" and detail["previous_digest"] == "sha256:aaa"


async def test_resource_increase_keeps_old_version_until_approved(api):
    await deployed(api)
    r = await register(api, labels={**LABELS, "mlapi.cpu": "4"}, digest="sha256:bbb")
    assert r.json()["action"] == "approval"
    detail = (await api.get("/v1/models/ecg", headers=H)).json()
    assert detail["state"] == "ready" and detail["current_digest"] == "sha256:aaa"
    await approve_first(api)
    detail = (await api.get("/v1/models/ecg", headers=H)).json()
    assert detail["current_digest"] == "sha256:bbb" and detail["approved_resources"]["cpu_m"] == 4000


async def test_sleep_wake_redeploy_rollback_delete_flow(api, redis):
    await deployed(api)
    r = await api.post("/v1/models/ecg/sleep", headers=H)
    assert r.status_code == 200 and r.json()["state"] == "sleeping"
    assert (await api.post("/v1/models/ecg/sleep", headers=H)).status_code == 409
    assert (await api.post("/v1/models/ecg/rollback", headers=H)).status_code == 409   # no previous version
    r = await api.post("/v1/models/ecg/wake", headers=H)
    assert r.status_code == 202 and r.json()["state"] == "starting"
    await drain(api)
    assert (await api.get("/v1/models/ecg", headers=H)).json()["state"] == "ready"
    assert (await api.post("/v1/models/ecg/wake", headers=H)).status_code == 409
    assert (await api.post("/v1/models/ecg/redeploy", headers=H)).status_code == 202
    await drain(api)
    r = await api.delete("/v1/models/ecg", headers=H)
    assert r.status_code == 200 and r.json()["state"] == "removed"
    assert await redis.exists("route:ecg") == 0


async def test_failed_deploy_is_visible_in_detail(api):
    api.backend.never_ready = {f"{IMAGE}@sha256:aaa", "sha256:aaa"}
    api.app.state.settings.deploy_timeout = 0.2
    await register(api)
    await approve_first(api)
    detail = (await api.get("/v1/models/ecg", headers=H)).json()
    assert detail["state"] == "failed" and detail["failed_digest"] == "sha256:aaa"
    assert detail["deployments"][0]["status"] == "failed" and "timed out" in detail["deployments"][0]["reason"]


async def test_config_patch(api):
    await deployed(api)
    r = await api.patch("/v1/models/ecg/config", headers=H, json={"idle_timeout_s": 120})
    assert r.status_code == 200 and r.json()["redeploy"] is False
    assert r.json()["model"]["config"] == {"idle_timeout_s": 120}
    r = await api.patch("/v1/models/ecg/config", headers=H, json={"env": {"HF_TOKEN": "t"}})
    assert r.json()["redeploy"] is True
    await drain(api)
    assert api.backend.models["ecg"].spec.env == {"HF_TOKEN": "t"}
    for bad in [{"env": {"FASTMLAPI_ROLE": "x"}}, {"idle_timeout_s": -5}, {"bogus": 1}]:
        assert (await api.patch("/v1/models/ecg/config", headers=H, json=bad)).status_code == 422


async def test_deploying_model_rejects_conflicting_operations(api):
    await deployed(api)
    api.backend.never_ready = {"sha256:bbb", f"{IMAGE}@sha256:bbb"}
    api.app.state.settings.deploy_timeout = 0.5
    await register(api, digest="sha256:bbb")           # starts a deploy that will take ~0.5s
    for method, path in [("post", "/v1/models/ecg/redeploy"), ("post", "/v1/models/ecg/sleep"), ("delete", "/v1/models/ecg")]:
        assert (await getattr(api, method)(path, headers=H)).status_code == 409
    await drain(api)
