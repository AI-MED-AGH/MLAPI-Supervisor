import pytest

from app import session as sessions
from app.auth import hash_admin_key
from tests.conftest import ADMIN_KEY

CSRF = {sessions.CSRF_HEADER: sessions.CSRF_VALUE}


async def login(api, key=ADMIN_KEY):
    return await api.post("/v1/auth/login", json={"key": key})


async def test_login_sets_httponly_strict_cookie(api):
    r = await login(api)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert sessions.COOKIE_NAME in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert ADMIN_KEY not in r.headers["set-cookie"]


async def test_wrong_key_is_401_and_sets_no_cookie(api):
    r = await login(api, "nope")
    assert r.status_code == 401 and "set-cookie" not in r.headers


async def test_cookie_authenticates_reads(api):
    await login(api)
    assert (await api.get("/v1/auth/me")).json() == {"authenticated": True}
    assert (await api.get("/v1/keys")).status_code == 200


async def test_cookie_mutation_needs_csrf_header(api):
    await login(api)
    body = {"name": "k", "allowed_models": ["m1"]}
    assert (await api.post("/v1/keys", json=body)).status_code == 403
    assert (await api.post("/v1/keys", json=body, headers=CSRF)).status_code == 201


async def test_admin_header_still_works_without_csrf_header(api):
    r = await api.post("/v1/keys", json={"name": "k", "allowed_models": ["m1"]}, headers={"X-Admin-Key": ADMIN_KEY})
    assert r.status_code == 201


async def test_wrong_header_is_not_rescued_by_valid_cookie(api):
    await login(api)
    assert (await api.get("/v1/keys", headers={"X-Admin-Key": "bad"})).status_code == 401


async def test_logout_clears_cookie(api):
    await login(api)
    assert (await api.post("/v1/auth/logout")).status_code == 403
    r = await api.post("/v1/auth/logout", headers=CSRF)
    assert r.status_code == 204
    api.cookies.clear()
    assert (await api.get("/v1/auth/me")).status_code == 401


async def test_no_cookie_no_header_is_401(api):
    assert (await api.get("/v1/auth/me")).status_code == 401


async def test_login_is_rate_limited(api):
    for _ in range(10):
        assert (await login(api, "bad")).status_code == 401
    r = await login(api, "bad")
    assert r.status_code == 429 and r.headers["retry-after"]


async def test_valid_key_logs_in_even_while_guessers_are_throttled(api):
    for _ in range(11):
        await login(api, "bad")
    assert (await login(api, ADMIN_KEY)).status_code == 204


def test_login_limiter_tracks_a_bounded_number_of_clients():
    limiter = sessions.LoginLimiter(max_clients=3)
    for i in range(10):
        limiter.record_failure(f"c{i}", now=float(i))
    assert len(limiter._failures) == 3 and "c9" in limiter._failures


async def test_session_dies_when_admin_key_is_removed(api):
    await login(api)
    api.app.state.settings.admin_api_keys = hash_admin_key("someone-else")
    assert (await api.get("/v1/keys")).status_code == 401


def test_token_expiry_and_tampering():
    h = hash_admin_key("k")
    token = sessions.issue("s", h, ttl=10, now=1000)
    assert sessions.verify("s", token, {h}, now=1005)
    assert not sessions.verify("s", token, {h}, now=1011)
    assert not sessions.verify("other", token, {h}, now=1005)
    assert not sessions.verify("s", token[:-2] + "00", {h}, now=1005)
    assert not sessions.verify("s", "garbage", {h}, now=1005)


async def test_ui_served_with_spa_fallback(settings, redis, sessionmaker, tmp_path):
    import httpx
    from app.main import create_app

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>admin</html>")
    (tmp_path / "assets" / "a.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("nope")
    settings.ui_dir = str(tmp_path)
    app = create_app(settings, redis=redis, sessionmaker=sessionmaker)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert "admin" in (await c.get("/admin")).text
        assert "admin" in (await c.get("/admin/models/foo")).text
        assert (await c.get("/admin/assets/a.js")).text == "console.log(1)"
        assert "nope" not in (await c.get("/admin/..%2fsecret.txt")).text
        assert "nope" not in (await c.get("/admin/%2e%2e/secret.txt")).text
