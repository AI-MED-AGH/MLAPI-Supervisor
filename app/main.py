from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI

from app.auth import require_admin
from app.config import Settings, get_settings
from app.db import Base, engine
from app.keys.api import router as keys_router
from app.watchman import watchmanRouter


def create_app(settings: Settings | None = None, *, redis=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = []
        if app.state.redis is None:
            app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            owned.append(app.state.redis.aclose)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
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
