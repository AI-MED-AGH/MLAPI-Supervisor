import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.keys.queries import create_key
from app.main import create_app
from app.migrate import upgrade_to_head


@pytest.fixture
async def file_db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'sup.db'}"
    engine = create_async_engine(url)
    yield url, async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


async def test_startup_migrates_and_rebuilds_keys(file_db, redis):
    url, sessionmaker = file_db
    await upgrade_to_head(url)
    async with sessionmaker() as s:
        key, _ = await create_key(s, name="k", allowed_models=["m1"], allow_all=False, expires_at=None)
        await s.commit()
    await redis.flushall()  # simulate a Redis restart that lost everything
    await redis.set("key:stale0000000", "{}")

    settings = Settings(_env_file=None, database_url=url, auto_migrate=True)
    app = create_app(settings, redis=redis, sessionmaker=sessionmaker)
    async with app.router.lifespan_context(app):
        assert await redis.exists(f"key:{key.id}") == 1
        assert await redis.exists("key:stale0000000") == 0


async def test_startup_migrates_an_empty_database(file_db, redis):
    url, sessionmaker = file_db
    settings = Settings(_env_file=None, database_url=url, auto_migrate=True)
    app = create_app(settings, redis=redis, sessionmaker=sessionmaker)
    async with app.router.lifespan_context(app):
        pass
    async with sessionmaker() as s:
        from app.keys.queries import list_keys

        assert list(await list_keys(s)) == []


async def test_auto_migrate_off_does_not_touch_schema(file_db, redis):
    url, sessionmaker = file_db
    settings = Settings(_env_file=None, database_url=url, auto_migrate=False)
    app = create_app(settings, redis=redis, sessionmaker=sessionmaker)
    with pytest.raises(Exception):  # no tables: rebuild_keys fails loudly instead of silently continuing
        async with app.router.lifespan_context(app):
            pass
