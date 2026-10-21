import hashlib
import logging

import pytest
from pydantic import ValidationError

from app import session as sessions
from app.config import Settings
from tests.test_models_api import IMAGE, LABELS

ALICE, BOB, LEGACY = "alice-secret-key", "bob-secret-key", "legacy-secret-key"
CSRF = {sessions.CSRF_HEADER: sessions.CSRF_VALUE}


def sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        admin_api_keys=f"alice:{sha(ALICE)}, bob:{sha(BOB)}, {sha(LEGACY)}",
        cluster_backend="fake", auto_migrate=False, run_background=False, deploy_timeout=0.5, deploy_poll_interval=0.01,
    )


def as_admin(raw):
    return {"X-Admin-Key": raw}


async def register(api, headers):
    api.inspector.add(IMAGE, LABELS, digest="sha256:aaa")
    return await api.post("/v1/models", headers=headers, json={"image": IMAGE, "source": "local"})


# ── configuration ──


def test_entries_may_be_named_or_plain_hashes():
    s = Settings(_env_file=None, admin_api_keys=f"alice:{sha(ALICE)},{sha(LEGACY)}")
    assert s.admin_hashes == {sha(ALICE), sha(LEGACY)}
    assert s.admin_names == {sha(ALICE): "alice", sha(LEGACY): "admin"}


@pytest.mark.parametrize("entry", ["has space:" + "a" * 64, ":" + "a" * 64, "x" * 41 + ":" + "a" * 64, "al/ice:" + "a" * 64])
def test_a_badly_named_entry_stops_the_supervisor_from_starting(entry):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_api_keys=entry)


# ── who did it ──


async def test_every_key_still_authenticates(api):
    for raw in (ALICE, BOB, LEGACY):
        assert (await api.get("/v1/keys", headers=as_admin(raw))).status_code == 200


async def test_an_approval_records_which_admin_made_it(api):
    await register(api, as_admin(BOB))
    pending = (await api.get("/v1/approvals", headers=as_admin(BOB))).json()[0]
    await api.post(f"/v1/approvals/{pending['id']}/approve", headers=as_admin(ALICE))
    done = [a for a in (await api.get("/v1/approvals?status_filter=all", headers=as_admin(BOB))).json()][0]
    assert done["decided_by"] == "alice"


async def test_a_rejection_records_the_admin_and_plain_keys_are_called_admin(api):
    await register(api, as_admin(BOB))
    pending = (await api.get("/v1/approvals", headers=as_admin(BOB))).json()[0]
    await api.post(f"/v1/approvals/{pending['id']}/reject", headers=as_admin(LEGACY), json={"note": "no"})
    done = (await api.get("/v1/approvals?status_filter=all", headers=as_admin(BOB))).json()[0]
    assert done["decided_by"] == "admin"


async def test_a_browser_session_acts_as_the_admin_who_logged_in(api):
    assert (await api.post("/v1/auth/login", json={"key": BOB})).status_code == 204
    await register(api, CSRF)  # through the cookie, like the browser UI
    pending = (await api.get("/v1/approvals")).json()[0]
    await api.post(f"/v1/approvals/{pending['id']}/approve", headers=CSRF)
    done = (await api.get("/v1/approvals?status_filter=all")).json()[0]
    assert done["decided_by"] == "bob"


# ── audit log ──


async def test_changes_are_logged_with_the_admin_but_reads_and_secrets_are_not(api, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    await api.get("/v1/keys", headers=as_admin(ALICE))
    r = await api.post("/v1/keys", headers=as_admin(ALICE), json={"name": "ci", "allowed_models": ["m1"]})
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "app.audit"]
    assert lines == ["admin=alice POST /v1/keys"]
    assert ALICE not in caplog.text and r.json()["key"] not in caplog.text


async def test_failed_authentication_is_logged_without_the_key(api, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    await api.post("/v1/keys", headers=as_admin("not-an-admin-key"), json={})
    assert "not-an-admin-key" not in caplog.text
    assert any("denied" in rec.getMessage() for rec in caplog.records if rec.name == "app.audit")
