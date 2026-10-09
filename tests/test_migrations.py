import sqlalchemy as sa

from app.db import Base
import app.keys.tables  # noqa: F401
import app.watchman.tables  # noqa: F401

try:
    import app.registry.tables  # noqa: F401
except ImportError:
    pass

from app.migrate import upgrade_to_head


async def test_migrations_create_exactly_the_model_tables(tmp_path):
    db_file = tmp_path / "m.db"
    await upgrade_to_head(f"sqlite+aiosqlite:///{db_file}")
    engine = sa.create_engine(f"sqlite:///{db_file}")
    try:
        migrated = set(sa.inspect(engine).get_table_names()) - {"alembic_version"}
        columns = {t: {c["name"] for c in sa.inspect(engine).get_columns(t)} for t in migrated}
    finally:
        engine.dispose()
    assert migrated == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        assert columns[name] == {c.name for c in table.columns}, name


async def test_upgrade_is_idempotent(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    await upgrade_to_head(url)
    await upgrade_to_head(url)
