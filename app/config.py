from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: str = "./data"
    database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    redis_admin_url: str | None = None
    model_redis_url: str | None = None  # how model containers reach Redis (defaults to redis_url's host)
    queue_acl_secret: str = ""
    admin_api_keys: str = ""
    auto_migrate: bool = True

    cluster_backend: str = "docker"  # docker | kubernetes | fake
    k8s_namespace: str = "mlapi-models"
    storage_class: str | None = None
    docker_network: str = "mlapi-models"
    docker_publish_ports: bool = False  # dev: publish model ports on 127.0.0.1 (Supervisor/Router run on the host)
    router_selector: str = "app=mlapi-router"
    supervisor_selector: str = "app=mlapi-supervisor"
    redis_selector: str = "app=mlapi-redis"
    k8s_image_pull_secret: str | None = "ghcr-pull"
    kubeconfig: str | None = None

    ghcr_org: str | None = None
    ghcr_token: str | None = None
    poll_interval: float = 300
    deploy_timeout: float = 1800
    deploy_poll_interval: float = 2.0
    gpu_wait_max: float = 86400
    max_restarts: int = 3
    reconcile_interval: float = 60
    queue_poll_interval: float = 5
    reaper_interval: float = 30
    run_background: bool = True
    default_idle_timeout: int = 900
    api_cpu_m: int = 100
    api_memory_bytes: int = 256 * 1024 * 1024
    log_level: str = "INFO"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir}/supervisor.db"

    @property
    def admin_hashes(self) -> set[str]:
        return {h.strip().lower() for h in self.admin_api_keys.split(",") if h.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
