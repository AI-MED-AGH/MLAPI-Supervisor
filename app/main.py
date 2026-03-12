from fastapi import FastAPI

app = FastAPI(
    title="MLAPI Supervisor",
    description="Supervisor controlling ML model instances",
    version="0.1.0",
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Welcome to MLAPI Supervisor"}


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
