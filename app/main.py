from fastapi import FastAPI
from app.watchman import watchmanRouter, engine, Base
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title="MLAPI Supervisor",
    description="Supervisor controlling ML model instances",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Welcome to MLAPI Supervisor"}


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

app.include_router(watchmanRouter, prefix="/observers")
