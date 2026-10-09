import os

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _prepare_sqlite_dir(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        os.makedirs(os.path.dirname(os.path.abspath(parsed.database)), exist_ok=True)


_db_url = get_settings().db_url
_prepare_sqlite_dir(_db_url)
engine = create_async_engine(_db_url, echo=False)
SessionLocal = async_sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)


async def get_session():
    async with SessionLocal() as session:
        yield session
