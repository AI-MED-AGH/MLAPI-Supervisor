import hashlib

from app.config import Settings


def make(**kw):
    return Settings(_env_file=None, **kw)


def test_database_url_defaults_to_sqlite_file_in_data_dir(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = make(data_dir="/tmp/mlapi-data")
    assert s.db_url == "sqlite+aiosqlite:////tmp/mlapi-data/supervisor.db"


def test_database_url_override_wins():
    s = make(database_url="postgresql+asyncpg://u:p@h/db")
    assert s.db_url == "postgresql+asyncpg://u:p@h/db"


def test_admin_hashes_parsed_and_normalised():
    h1 = hashlib.sha256(b"a").hexdigest()
    h2 = hashlib.sha256(b"b").hexdigest()
    s = make(admin_api_keys=f" {h1.upper()} , {h2},,")
    assert s.admin_hashes == {h1, h2}


def test_no_admin_hashes_by_default():
    assert make().admin_hashes == set()


def test_defaults():
    s = make()
    assert s.auto_migrate is True
    assert s.cluster_backend == "docker"
    assert s.poll_interval == 300
    assert s.deploy_timeout == 1800


def test_reads_environment(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://:pw@r:6379/2")
    monkeypatch.setenv("CLUSTER_BACKEND", "kubernetes")
    s = Settings(_env_file=None)
    assert s.redis_url == "redis://:pw@r:6379/2"
    assert s.cluster_backend == "kubernetes"


def test_queue_redis_settings_default_to_the_shared_redis():
    s = make()
    assert s.queue_redis_url is None and s.worker_heartbeat_grace == 10.0
    assert s.redis_selector == "app=mlapi-queue-redis"


def test_wake_retry_cooldown_default():
    assert make().queue_wake_retry_after == 60
