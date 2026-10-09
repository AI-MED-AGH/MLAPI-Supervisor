from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI

from app.auth import require_admin
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.keys.api import router as keys_router
from app.migrate import upgrade_to_head
from app.redis_sync.keys import rebuild_keys
from app.watchman import watchmanRouter


def create_app(settings: Settings | None = None, *, redis=None, sessionmaker=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = []
        if app.state.redis is None:
            app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            owned.append(app.state.redis.aclose)
        try:
            if settings.auto_migrate:
                await upgrade_to_head(settings.db_url)
            async with app.state.sessionmaker() as session:
                await rebuild_keys(app.state.redis, session)
            yield
        finally:
            for close in owned:
                await close()

    app = FastAPI(
        title="MLAPI Supervisor",
        description="Supervisor controlling ML model instances",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.redis = redis
    app.state.sessionmaker = sessionmaker or SessionLocal

    @app.get("/")
    def root() -> dict[str, str]:
        return {"message": "Welcome to MLAPI Supervisor"}

    @app.get("/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(watchmanRouter, prefix="/v1/observers", dependencies=[Depends(require_admin)])
    app.include_router(keys_router)
    return app


app = create_app()
