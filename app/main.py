import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth import require_admin
from app.auth_api import router as auth_router
from app.session import LoginLimiter
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.keys.api import router as keys_router
from app.lifecycle.runner import build_background
from app.services import build_services
from app.migrate import upgrade_to_head
from app.registry.api import approvals_router, models_router
from app.registry.errors import Conflict, Invalid, NotFound
from app.registry.resources import ResourceError
from app.redis_sync.keys import rebuild_keys
from app.watchman import watchmanRouter


def create_app(
    settings: Settings | None = None,
    *,
    redis=None,
    sessionmaker=None,
    backend=None,
    probe=None,
    inspector=None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = []
        background = None
        if app.state.redis is None:
            app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            owned.append(app.state.redis.aclose)
        try:
            if settings.auto_migrate:
                await upgrade_to_head(settings.db_url)
            async with app.state.sessionmaker() as session:
                await rebuild_keys(app.state.redis, session)
            if settings.run_background:
                app.state.services = build_services(app)
                await app.state.services.deployer.recover_interrupted()
                if app.state.services.queue_access is not None:
                    await app.state.services.queue_access.verify_server()
                background = build_background(app.state.services, app.state)
                background.start()
            yield
        finally:
            if background is not None:
                await background.stop()
                await app.state.services.events.drain()
                await app.state.services.aclose()
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
    app.state.backend = backend
    app.state.probe = probe
    app.state.inspector = inspector
    app.state.services = None
    app.state.session_secret = settings.session_secret or secrets.token_hex(32)
    app.state.login_limiter = LoginLimiter()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"message": "Welcome to MLAPI Supervisor"}

    @app.get("/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(watchmanRouter, prefix="/v1/observers", dependencies=[Depends(require_admin)])
    app.include_router(auth_router)
    app.include_router(keys_router)
    app.include_router(models_router)
    app.include_router(approvals_router)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Invalid)
    @app.exception_handler(ResourceError)
    async def _invalid(request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    _mount_ui(app, settings)
    return app


def _mount_ui(app: FastAPI, settings: Settings) -> None:
    """Serves the built admin UI at /admin, with the SPA fallback to index.html."""
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    root = Path(settings.ui_dir) if settings.ui_dir else None
    if root is None or not (root / "index.html").is_file():
        return
    root = root.resolve()
    if (root / "assets").is_dir():
        app.mount("/admin/assets", StaticFiles(directory=root / "assets"), name="admin-assets")

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/{path:path}", include_in_schema=False)
    async def admin_ui(path: str = ""):
        candidate = (root / path).resolve()
        headers = {"Cache-Control": "no-cache", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate, headers=headers)
        return FileResponse(root / "index.html", headers=headers)


app = create_app()
