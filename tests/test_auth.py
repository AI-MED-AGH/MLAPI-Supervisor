import hashlib

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.auth import hash_admin_key, require_admin
from app.config import Settings


def make_client(admin_keys: str):
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, admin_api_keys=admin_keys)

    @app.get("/secret", dependencies=[Depends(require_admin)])
    async def secret():
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


def test_hash_admin_key_is_sha256():
    assert hash_admin_key("abc") == hashlib.sha256(b"abc").hexdigest()


async def test_valid_key_passes():
    async with make_client(hash_admin_key("k1")) as c:
        assert (await c.get("/secret", headers={"X-Admin-Key": "k1"})).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ""}])
async def test_missing_or_wrong_key_is_401_with_uniform_body(headers):
    async with make_client(hash_admin_key("k1")) as c:
        r = await c.get("/secret", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or missing admin key"}


async def test_second_configured_key_also_works():
    keys = f"{hash_admin_key('k1')},{hash_admin_key('k2')}"
    async with make_client(keys) as c:
        assert (await c.get("/secret", headers={"X-Admin-Key": "k2"})).status_code == 200


async def test_no_configured_keys_rejects_everything():
    async with make_client("") as c:
        assert (await c.get("/secret", headers={"X-Admin-Key": ""})).status_code == 401
        assert (await c.get("/secret", headers={"X-Admin-Key": "anything"})).status_code == 401
