import json
import re

import pytest

from app.queue_access.acl import QueueAccess, QueueAccessError
from tests.support import _ready_model, get

SECRET = "master-secret-for-tests"


class RecordingRedis:
    def __init__(self):
        self.commands = []
        self.fail = None
        self.version = "7.4.0"

    async def info(self, section=None):
        return {"redis_version": self.version}

    async def execute_command(self, *args):
        self.commands.append(args)
        if self.fail:
            raise self.fail
        return b"OK"


def make(redis=None, secret=SECRET, base="redis://redis.internal:6380/2", dedicated=True):
    redis = redis or RecordingRedis()
    return QueueAccess(redis, secret=secret, model_redis_url=base, dedicated=dedicated), redis


async def test_user_is_limited_to_the_models_key_prefix_and_safe_commands():
    qa, redis = make()
    await qa.provision("ecg")
    cmd = redis.commands[0]
    assert cmd[:3] == ("ACL", "SETUSER", "m_ecg")
    rules = list(cmd[3:])
    assert rules[0] == "reset" and "on" in rules
    assert "~fastmlapi:ecg:*" in rules
    assert [r for r in rules if r.startswith("~")] == ["~fastmlapi:ecg:*"]      # exactly one key pattern
    assert "resetchannels" in rules and not any(r.startswith("&") for r in rules)  # no pub/sub channels
    assert rules.index("-@all") < rules.index("+@list")                          # deny everything, then allow
    for dangerous in ("+@admin", "+@dangerous", "+@scripting", "+@pubsub", "+@all", "+@keyspace", "+@connection",
                      "+flushall", "+flushdb", "+swapdb", "+config", "+acl", "+keys", "+eval", "+shutdown", "+debug"):
        assert dangerous not in rules


def test_only_data_structure_categories_and_named_commands_are_granted():
    """Categories can hide key-less destructive commands (@keyspace contains FLUSHALL), so only these are allowed."""
    from app.queue_access.acl import MODEL_COMMANDS

    categories = {r for r in MODEL_COMMANDS if r.startswith("+@")}
    assert categories == {"+@list", "+@hash", "+@string", "+@set", "+@sortedset"}
    named = {r for r in MODEL_COMMANDS if r.startswith("+") and not r.startswith("+@")}
    assert not named & {"+flushall", "+flushdb", "+swapdb", "+keys", "+randomkey", "+move", "+rename", "+dbsize", "+info",
                        "+eval", "+eval_ro", "+script|flush", "+script|kill", "+script", "+shutdown", "+config", "+acl", "+debug"}


def test_commands_fastmlapi_needs_are_allowed():
    """Found by running the real fastmlapi worker against a real Redis: it uses TIME, Lua scripts (EVALSHA + SCRIPT LOAD),
    MULTI/EXEC pipelines and SCAN. Without these the queue cannot work."""
    from app.queue_access.acl import MODEL_COMMANDS

    for needed in ("+time", "+evalsha", "+script|load", "+multi", "+exec", "+scan"):
        assert needed in MODEL_COMMANDS


async def test_returned_url_points_at_the_model_redis_with_the_model_user():
    qa, _ = make()
    url = await qa.provision("ecg")
    match = re.fullmatch(r"redis://m_ecg:([A-Za-z0-9_-]{43})@redis\.internal:6380/2", url)
    assert match, url


async def test_password_is_deterministic_so_a_redis_restart_can_be_repaired():
    qa1, _ = make()
    qa2, _ = make()
    assert await qa1.provision("ecg") == await qa2.provision("ecg") == await qa1.provision("ecg")


async def test_passwords_differ_per_model_and_per_master_secret():
    qa, _ = make()
    other, _ = make(secret="a-different-master-secret")
    assert await qa.provision("ecg") != await qa.provision("xray")
    assert await qa.provision("ecg") != await other.provision("ecg")


async def test_password_is_sent_to_redis_but_never_logged(caplog):
    qa, redis = make()
    with caplog.at_level("DEBUG"):
        url = await qa.provision("ecg")
    password = url.split(":")[2].split("@")[0]
    assert f">{password}" in redis.commands[0]
    assert password not in caplog.text


async def test_existing_credentials_in_the_base_url_are_replaced():
    qa, _ = make(base="redis://:adminpass@redis:6379/0")
    url = await qa.provision("ecg")
    assert "adminpass" not in url and url.startswith("redis://m_ecg:")


async def test_remove_deletes_the_user_and_tolerates_failures():
    qa, redis = make()
    await qa.remove("ecg")
    assert redis.commands[-1] == ("ACL", "DELUSER", "m_ecg")
    redis.fail = RuntimeError("redis down")
    await qa.remove("ecg")                         # must not raise: removal is best effort


@pytest.mark.parametrize("name", ["", "Bad", "a b", "a*", "../x", "x\n", "a" * 50, "a:b"])
async def test_invalid_names_never_reach_redis(name):
    qa, redis = make()
    with pytest.raises(QueueAccessError):
        await qa.provision(name)
    assert redis.commands == []


async def test_missing_master_secret_is_a_clear_error():
    qa, redis = make(secret="")
    with pytest.raises(QueueAccessError, match="QUEUE_ACL_SECRET"):
        await qa.provision("ecg")
    assert redis.commands == []


async def test_redis_failure_becomes_queue_access_error():
    qa, redis = make()
    redis.fail = RuntimeError("NOAUTH")
    with pytest.raises(QueueAccessError):
        await qa.provision("ecg")


# ───────────── wiring into deploys ─────────────
async def test_queue_deploy_passes_the_acl_url_to_the_backend(env):
    qa, redis = make()
    env.deployer._queue_access = qa
    await _ready_model(env, mode="queue")
    spec = env.backend.models["ecg"].spec
    assert spec.queue_url.startswith("redis://m_ecg:")
    assert (await get(env)).state == "sleeping"


async def test_queue_model_cannot_deploy_without_acl_support(env):
    env.deployer._queue_access = None
    await _ready_model(env, mode="queue")
    model = await get(env)
    assert model.state == "failed" and "QUEUE_ACL_SECRET" in model.state_detail


async def test_sync_models_never_touch_the_acl_manager(env):
    qa, redis = make()
    env.deployer._queue_access = qa
    await _ready_model(env)
    assert redis.commands == []


async def test_removing_a_queue_model_deletes_its_acl_user(env):
    qa, redis = make()
    env.deployer._queue_access = qa
    await _ready_model(env, mode="queue")
    from app.registry.queries import get_model_by_name
    async with env.sm() as s:
        await env.deployer.remove(s, await get_model_by_name(s, "ecg"))
        await s.commit()
    assert ("ACL", "DELUSER", "m_ecg") in redis.commands


async def test_reconciler_reprovisions_users_after_a_redis_restart(env):
    from app.lifecycle.reconciler import Reconciler

    qa, redis = make()
    env.deployer._queue_access = qa
    await _ready_model(env, mode="queue")
    redis.commands.clear()
    await Reconciler(sessionmaker=env.sm, deployer=env.deployer, redis=env.redis, backend=env.backend,
                     probe=env.probe, queue_access=qa).tick()
    assert [c[:3] for c in redis.commands] == [("ACL", "SETUSER", "m_ecg")]


# ───────────── SCAN is only safe where Redis filters it by key permissions (Redis >= 7) ─────────────
class InfoRedis(RecordingRedis):
    def __init__(self, version):
        super().__init__()
        self.version = version

    async def info(self, section=None):
        return {"redis_version": self.version}


@pytest.mark.parametrize("version", ["6.2.14", "5.0.7", "6.0.0"])
async def test_old_redis_versions_are_refused_for_queue_models(version):
    qa, redis = make(redis=InfoRedis(version))
    await qa.verify_server()
    with pytest.raises(QueueAccessError, match="7"):
        await qa.provision("ecg")
    assert redis.commands == []


@pytest.mark.parametrize("version", ["7.0.0", "7.4.1", "8.0.2"])
async def test_redis_7_and_newer_are_accepted(version):
    qa, redis = make(redis=InfoRedis(version))
    await qa.verify_server()
    assert (await qa.provision("ecg")).startswith("redis://m_ecg:")


async def test_a_server_whose_version_cannot_be_read_is_refused_not_trusted():
    class Down(RecordingRedis):
        async def info(self, section=None):
            raise RuntimeError("down")

    qa, redis = make(redis=Down())
    await qa.verify_server()                         # never raises at startup ...
    with pytest.raises(QueueAccessError, match="version"):
        await qa.provision("ecg")                    # ... but provisioning fails closed
    assert redis.commands == []


async def test_verification_is_retried_so_a_late_starting_redis_recovers():
    class Late(RecordingRedis):
        up = False

        async def info(self, section=None):
            if not self.up:
                raise RuntimeError("starting")
            return {"redis_version": "7.4.0"}

    qa, redis = make(redis=Late())
    await qa.verify_server()
    with pytest.raises(QueueAccessError):
        await qa.provision("ecg")
    redis.up = True
    assert (await qa.provision("ecg")).startswith("redis://m_ecg:")


async def test_queue_models_refuse_to_share_a_redis_with_routes_and_keys():
    """SCAN would list the names of every key (API key ids, model names) in a shared Redis, and a model's Lua script can
    hang it for everyone, so a dedicated queue Redis is mandatory."""
    qa, redis = make(dedicated=False)
    with pytest.raises(QueueAccessError, match="QUEUE_REDIS_URL"):
        await qa.provision("ecg")
    assert redis.commands == []


def test_services_only_mark_the_queue_redis_dedicated_when_one_is_configured():
    from types import SimpleNamespace

    from app.config import Settings
    from app.services import build_queue_access

    shared = SimpleNamespace(settings=Settings(_env_file=None, queue_acl_secret="s"), redis=RecordingRedis())
    assert build_queue_access(shared, shared.redis)._dedicated is False
    dedicated = SimpleNamespace(
        settings=Settings(_env_file=None, queue_acl_secret="s", queue_redis_url="redis://:p@queue-redis:6379/0"),
        redis=RecordingRedis(),
    )
    assert build_queue_access(dedicated, RecordingRedis())._dedicated is True
