import re
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_ADMIN_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,40}")


def parse_admin_entries(raw: str) -> dict[str, str]:
    """ADMIN_API_KEYS is a comma-separated list of `name:sha256hash` (or just `sha256hash`, which is called "admin").
    Returns {hash: name}. A bad name raises ValueError: a typo here must stop the Supervisor, not weaken auditing."""
    entries: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, separator, digest = entry.rpartition(":")
        if not separator:
            name, digest = "admin", entry
        elif not _ADMIN_NAME_RE.fullmatch(name):
            raise ValueError("admin key names use letters, digits, '_', '.' and '-' (1 to 40 characters)")
        entries[digest.strip().lower()] = name
    return entries


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
    # 0 = keys may live forever. Otherwise every key needs an expiry no further away than this many seconds.
    key_max_lifetime_seconds: int = 0
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
    # One network per model, so models cannot reach each other. Containers named in DOCKER_PEERS (the Router and the
    # Supervisor, when they run in Docker too) are attached to every model's network. Queue-mode models also join
    # DOCKER_NETWORK, where the queue Redis is.
    docker_isolate_models: bool = False
    docker_peers: str = ""
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
    telemetry_interval: float = 5  # seconds between resource-use measurements; 0 turns telemetry off
    run_background: bool = True
    # What an image's own rate labels (mlapi.rate.*) may ask for. Admin values are not capped.
    limit_ceiling_rpm_per_key: int = 600
    limit_ceiling_rpm_total: int = 6000
    limit_ceiling_concurrency: int = 64
    limit_ceiling_max_pending: int = 10_000
    default_max_pending: int = 1000  # queued jobs a queue-mode model accepts before refusing more
    default_idle_timeout: int = 900
    api_cpu_m: int = 100
    api_memory_bytes: int = 256 * 1024 * 1024
    enable_docs: bool = False  # /docs, /redoc and /openapi.json describe the whole admin API: off unless asked for
    log_level: str = "INFO"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir}/supervisor.db"

    @property
    def docker_peer_names(self) -> list[str]:
        return [name.strip() for name in self.docker_peers.split(",") if name.strip()]

    @field_validator("admin_api_keys")
    @classmethod
    def _admin_entries_are_valid(cls, value: str) -> str:
        parse_admin_entries(value)
        return value

    @property
    def admin_names(self) -> dict[str, str]:
        """{hash: name} for every configured admin key."""
        return parse_admin_entries(self.admin_api_keys)

    @property
    def admin_hashes(self) -> set[str]:
        return set(self.admin_names)


@lru_cache
def get_settings() -> Settings:
    return Settings()
