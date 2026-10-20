from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: str = "./data"
    database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    # Queue-mode models run untrusted code against Redis (Lua scripts can hang a Redis server), so their queues
    # belong on a separate instance. Unset = share redis_url (fine for development only).
    queue_redis_url: str | None = None
    # Models built with an older fastmlapi (Lua scripts, SCAN) need broader Redis permissions and a dedicated queue Redis.
    queue_legacy_acl: bool = False
    model_redis_url: str | None = None  # how model containers reach the queue Redis
    queue_wake_retry_after: float = 60  # after a failed worker start, wait this long before trying again
    worker_heartbeat_grace: float = 10.0  # wait this long for a worker heartbeat before trusting the container
    queue_acl_secret: str = ""
    admin_api_keys: str = ""
    auto_migrate: bool = True
    # Admin UI sessions. Empty secret = a random one per process (sessions end on restart; set it to keep them).
    session_secret: str = ""
    session_ttl: float = 8 * 3600
    session_cookie_secure: bool | None = None  # None = secure when the request came in over https
    ui_dir: str | None = None  # built admin UI (index.html + assets); served at /admin when present

    cluster_backend: str = "docker"  # docker | kubernetes | fake
    k8s_namespace: str = "mlapi-models"
    storage_class: str | None = None
    docker_network: str = "mlapi-models"
    docker_publish_ports: bool = False  # dev: publish model ports on 127.0.0.1 (Supervisor/Router run on the host)
    router_selector: str = "app=mlapi-router"
    supervisor_selector: str = "app=mlapi-supervisor"
    redis_selector: str = "app=mlapi-queue-redis"  # what queue-mode pods may reach
    k8s_image_pull_secret: str | None = "ghcr-pull"
    k8s_ephemeral_storage: str = "2Gi"  # per-container scratch space (writable layer, /tmp)
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
