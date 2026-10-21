import logging

import httpx
import pytest

from app.main import create_app
from tests.conftest import ADMIN_KEY


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_the_api_description_is_not_served_by_default(api, path):
    assert (await api.get(path)).status_code == 404


async def test_the_api_description_can_be_turned_on(settings, redis, sessionmaker):
    settings.enable_docs = True
    app = create_app(settings, redis=redis, sessionmaker=sessionmaker)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sup") as client:
        assert (await client.get("/openapi.json")).status_code == 200


async def test_logins_are_logged_without_the_key(api, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    await api.post("/v1/auth/login", json={"key": "wrong-key-value"})
    await api.post("/v1/auth/login", json={"key": ADMIN_KEY})
    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]
    assert lines[0].startswith("denied login from ")
    assert lines[1] == "admin=admin login"
    assert "wrong-key-value" not in caplog.text and ADMIN_KEY not in caplog.text
