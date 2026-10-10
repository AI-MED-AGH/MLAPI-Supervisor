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


def make(redis=None, secret=SECRET, base="redis://redis.internal:6380/2", dedicated=True, legacy=False):
    redis = redis or RecordingRedis()
    return QueueAccess(redis, secret=secret, model_redis_url=base, dedicated=dedicated, legacy=legacy), redis


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
    assert rules.index("-@all") < rules.index("+lmove")                          # deny everything, then allow
    for dangerous in ("+@admin", "+@dangerous", "+@scripting", "+@pubsub", "+@all", "+@keyspace", "+@connection",
                      "+flushall", "+flushdb", "+swapdb", "+config", "+acl", "+keys", "+eval", "+shutdown", "+debug"):
        assert dangerous not in rules


# the command list of fastmlapi's Redis backend (fastmlapi.queue.redis_backend.REDIS_COMMANDS_USED), copied on purpose: if
# the library starts using another command this test fails and someone has to decide to grant it
FASTMLAPI_COMMANDS = (
    "LMOVE LPUSH RPUSH LREM LRANGE LLEN LPOS HSET HSETNX HGET HMGET HGETALL HDEL HINCRBY EXPIRE SADD SREM SMEMBERS DEL EXISTS "
    "WATCH UNWATCH MULTI EXEC DISCARD TIME PING"
).split()


def test_strict_mode_grants_exactly_the_commands_the_library_uses_and_no_categories():
    """Categories hide expensive or destructive commands (@keyspace has FLUSHALL, @string has LCS, @set has SUNIONSTORE)."""
    from app.queue_access.acl import MODEL_COMMANDS

    granted = {r for r in MODEL_COMMANDS if r.startswith("+")}
    assert not {r for r in granted if r.startswith("+@")}, "no command categories in strict mode"
    names = {r[1:].upper() for r in granted if "|" not in r}
    assert set(FASTMLAPI_COMMANDS) <= names
    extra = names - set(FASTMLAPI_COMMANDS)
    assert extra <= {"HELLO", "AUTH"}, f"unexpected extra commands: {extra}"
    sub = {r for r in granted if "|" in r}
    assert sub <= {"+client|setinfo", "+client|setname", "+client|id"}


def test_legacy_mode_keeps_the_data_structure_categories_for_older_libraries():
    from app.queue_access.acl import commands_for

    legacy = commands_for(legacy=True)
    assert {"+@list", "+@hash", "+@set"} <= set(legacy)
    assert not {"+@keyspace", "+@dangerous", "+@admin", "+@scripting", "+eval"} & set(legacy)


def test_commands_fastmlapi_needs_are_allowed_and_nothing_scripting_or_scanning():
    """fastmlapi's Redis backend uses lists/hashes/sets, WATCH/MULTI/EXEC transactions, LMOVE and TIME: no Lua, no SCAN."""
    from app.queue_access.acl import MODEL_COMMANDS

    for needed in ("+time", "+multi", "+exec", "+watch", "+unwatch", "+discard", "+lmove", "+hset", "+smembers"):
        assert needed in MODEL_COMMANDS
    for never in ("+eval", "+evalsha", "+script|load", "+scan", "+@scripting", "+keys", "+@keyspace"):
        assert never not in MODEL_COMMANDS


def test_legacy_mode_adds_the_scripting_and_scan_commands_older_fastmlapi_versions_need():
    from app.queue_access.acl import LEGACY_COMMANDS, MODEL_COMMANDS, commands_for

    assert LEGACY_COMMANDS == ["+evalsha", "+script|load", "+scan"]
    assert commands_for(legacy=False) == MODEL_COMMANDS
    assert set(LEGACY_COMMANDS) <= set(commands_for(legacy=True)) and "+eval" not in commands_for(legacy=True)


async def test_a_legacy_user_gets_the_extra_commands_and_a_strict_user_does_not():
    qa, redis = make(legacy=True)
    await qa.provision("ecg")
    assert "+evalsha" in redis.commands[0] and "+scan" in redis.commands[0]
    qa, redis = make(legacy=False)
    await qa.provision("ecg")
    assert "+evalsha" not in redis.commands[0] and "+scan" not in redis.commands[0]


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


@pytest.mark.parametrize("legacy", [False, True])
async def test_queue_models_never_share_the_redis_that_holds_routes_and_keys(legacy):
    """Redis cannot cap memory per user: one model filling its keys would exhaust a shared instance, and with noeviction the
    platform's own route and key writes would start failing. Queues live on a dedicated, memory-capped Redis."""
    qa, redis = make(dedicated=False, legacy=legacy)
    with pytest.raises(QueueAccessError, match="QUEUE_REDIS_URL"):
        await qa.provision("ecg")
    assert redis.commands == []


@pytest.mark.parametrize("legacy", [False, True])
async def test_a_dedicated_queue_redis_is_accepted(legacy):
    qa, _ = make(dedicated=True, legacy=legacy)
    assert (await qa.provision("ecg")).startswith("redis://m_ecg:")


def test_services_pass_the_legacy_switch_through():
    from types import SimpleNamespace

    from app.config import Settings
    from app.services import build_queue_access

    state = SimpleNamespace(settings=Settings(_env_file=None, queue_acl_secret="s", queue_legacy_acl=True), redis=RecordingRedis())
    assert build_queue_access(state, state.redis)._legacy is True
    state.settings = Settings(_env_file=None, queue_acl_secret="s")
    assert build_queue_access(state, state.redis)._legacy is False


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
