from fastapi import FastAPI

from app.routers import health, instances

app = FastAPI(
    title="MLAPI Supervisor",
    description="Supervisor controlling ML model instances",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(instances.router)
