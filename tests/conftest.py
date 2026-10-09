import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import hashlib

import httpx
import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings

ADMIN_KEY = "admin-secret-for-tests"
ADMIN_HEADERS = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        admin_api_keys=hashlib.sha256(ADMIN_KEY.encode()).hexdigest(),
        cluster_backend="fake",
        auto_migrate=False,
    )


@pytest_asyncio.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def db_engine():
    from app.db import Base
    import app.keys.tables  # noqa: F401  (register tables)
    import app.watchman.tables  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def session(sessionmaker):
    async with sessionmaker() as s:
        yield s


@pytest_asyncio.fixture
async def api(settings, redis, sessionmaker):
    """HTTP client on a fresh app wired to in-memory DB and fake Redis."""
    from app.db import get_session
    from app.main import create_app

    app = create_app(settings, redis=redis)

    async def override_session():
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sup") as c:
        c.app = app
        yield c
