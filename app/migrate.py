"""Runs Alembic migrations programmatically (used at startup and by tests)."""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

_ROOT = Path(__file__).resolve().parent.parent


def alembic_config(db_url: str | None = None) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return cfg


def _upgrade(db_url: str | None) -> None:
    command.upgrade(alembic_config(db_url), "head")


async def upgrade_to_head(db_url: str | None = None) -> None:
    # env.py starts its own event loop, so run in a worker thread
    await asyncio.to_thread(_upgrade, db_url)
