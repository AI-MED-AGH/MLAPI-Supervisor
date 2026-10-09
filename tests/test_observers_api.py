import pytest

from tests.conftest import ADMIN_HEADERS

OBS = "/v1/observers"


@pytest.mark.parametrize(
    "method,path",
    [("get", OBS + "/"), ("post", OBS + "/"), ("delete", OBS + "/1")],
)
async def test_every_observer_endpoint_requires_admin(api, method, path):
    kwargs = {"json": {"webhook_url": "http://h/x", "event_types": ["a"]}} if method == "post" else {}
    assert (await getattr(api, method)(path, **kwargs)).status_code == 401


async def test_old_unversioned_path_is_gone(api):
    r = await api.post("/observers/", headers=ADMIN_HEADERS, json={"webhook_url": "http://h/x", "event_types": ["a"]})
    assert r.status_code == 404


@pytest.mark.parametrize(
    "url",
    ["0.0.0.0:8000", "ftp://h/x", "javascript:alert(1)", "http://", "file:///etc/passwd", "", "http:// bad"],
)
async def test_webhook_url_must_be_http_or_https(api, url):
    r = await api.post(OBS + "/", headers=ADMIN_HEADERS, json={"webhook_url": url, "event_types": ["a"]})
    assert r.status_code == 422


@pytest.mark.parametrize("events", [[], [""], ["ok", ""]])
async def test_event_types_must_be_non_empty_strings(api, events):
    r = await api.post(OBS + "/", headers=ADMIN_HEADERS, json={"webhook_url": "http://h/x", "event_types": events})
    assert r.status_code == 422


async def test_subscribe_list_and_delete(api):
    r = await api.post(OBS + "/", headers=ADMIN_HEADERS, json={"webhook_url": "https://mon.example/hook", "event_types": ["deploy.failed", "model.sleeping"]})
    assert r.status_code == 201
    r = await api.post(OBS + "/", headers=ADMIN_HEADERS, json={"webhook_url": "https://mon.example/hook", "event_types": ["deploy.failed", "model.woke"]})
    assert r.status_code == 201  # second call adds only the new event type

    listing = (await api.get(OBS + "/", headers=ADMIN_HEADERS)).json()
    assert len(listing) == 1
    assert listing[0]["webhook_url"] == "https://mon.example/hook"
    assert sorted(listing[0]["event_types"]) == ["deploy.failed", "model.sleeping", "model.woke"]

    assert (await api.delete(f"{OBS}/{listing[0]['id']}", headers=ADMIN_HEADERS)).status_code == 204
    assert (await api.get(OBS + "/", headers=ADMIN_HEADERS)).json() == []


async def test_delete_unknown_observer_is_404(api):
    assert (await api.delete(OBS + "/999", headers=ADMIN_HEADERS)).status_code == 404


async def test_duplicate_event_types_are_stored_once(api):
    await api.post(OBS + "/", headers=ADMIN_HEADERS, json={"webhook_url": "http://h/x", "event_types": ["a", "a", "b", "a"]})
    listing = (await api.get(OBS + "/", headers=ADMIN_HEADERS)).json()
    assert listing[0]["event_types"] == ["a", "b"]


async def test_webhook_urls_never_reach_the_logs(api, caplog):
    secret_url = "https://hooks.example/services/T000/B000/SECRETTOKEN"
    with caplog.at_level("DEBUG"):
        await api.post(OBS + "/", headers=ADMIN_HEADERS, json={"webhook_url": secret_url, "event_types": ["a"]})
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("app")]   # third-party DEBUG logs are off in production
    assert ours and not any("SECRETTOKEN" in m for m in ours)
