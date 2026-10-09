import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import Base
import app.keys.tables  # noqa: F401
import app.watchman.tables  # noqa: F401

try:  # later tasks add more table modules
    import app.registry.tables  # noqa: F401
except ImportError:
    pass

target_metadata = Base.metadata


def _url() -> str:
    url = context.config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app.config import get_settings

    return get_settings().db_url


def _do_run(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite-friendly ALTERs
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async():
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
