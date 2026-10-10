import base64
import hashlib
import hmac
import logging
import re
from urllib.parse import quote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,38}[a-z0-9])?")

# Everything fastmlapi's queue needs (lists, hashes, key expiry) and nothing that touches other keys or the server.
# Do NOT grant whole categories such as @keyspace or @dangerous: @keyspace contains FLUSHALL, FLUSHDB and SWAPDB,
# which take no key argument and so escape the `~fastmlapi:<name>:*` restriction. If a new fastmlapi release needs
# more commands, add them here one by one.
DATA_CATEGORIES = ["+@list", "+@hash", "+@string", "+@set", "+@sortedset"]
KEY_COMMANDS = [
    "+del", "+unlink", "+exists", "+expire", "+pexpire", "+expireat", "+pexpireat", "+persist", "+ttl", "+pttl", "+type",
]
CONNECTION_COMMANDS = ["+ping", "+hello", "+auth", "+echo", "+client|setname", "+client|setinfo", "+client|id"]
# What the CURRENT fastmlapi Redis backend needs (fastmlapi.queue.redis_backend.REDIS_COMMANDS_USED): lists, hashes, sets,
# optimistic transactions (WATCH/MULTI/EXEC), LMOVE and TIME. No Lua scripts and no SCAN, so a model cannot hang the server
# with a script, cannot list the names of other tenants' keys, and the key prefix below is enforced for every command it can
# run. Requires Redis >= 7 (checked at provision time, fail closed).
QUEUE_COMMANDS = ["+time", "+multi", "+exec", "+discard", "+watch", "+unwatch"]
MODEL_COMMANDS = ["-@all", *DATA_CATEGORIES, *KEY_COMMANDS, *CONNECTION_COMMANDS, *QUEUE_COMMANDS]

# OLDER fastmlapi versions used Lua scripts (register_script = EVALSHA + SCRIPT LOAD) and SCAN. Found by running the real
# library against Redis 7.4. Redis enforces the key prefix and command list inside scripts, BUT SCAN is not filtered by key
# permissions (a model can list the NAMES of every key in its Redis) and a script can hang the server for everyone. Hence
# legacy mode refuses a Redis that is shared with routes and API keys. Opt in with QUEUE_LEGACY_ACL=true; EVAL and
# SCRIPT FLUSH/KILL stay denied either way.
LEGACY_COMMANDS = ["+evalsha", "+script|load", "+scan"]


def commands_for(*, legacy: bool) -> list[str]:
    return [*MODEL_COMMANDS, *LEGACY_COMMANDS] if legacy else list(MODEL_COMMANDS)


class QueueAccessError(Exception):
    pass


class QueueAccess:
    """Per-model Redis ACL users: a model container can only touch `fastmlapi:<name>:*`."""

    def __init__(self, redis, *, secret: str, model_redis_url: str, dedicated: bool = True, legacy: bool = False):
        self._redis = redis
        self._secret = secret
        self._base = model_redis_url
        self._unsafe: str | None = None
        self._verified = False
        self._dedicated = dedicated  # True only when the queue Redis holds nothing but queue data
        self._legacy = legacy

    async def verify_server(self) -> None:
        """Checks the Redis version (>= 7 enforces the ACL inside scripts). Never raises; provision() fails closed."""
        try:
            version = str((await self._redis.info("server")).get("redis_version", ""))
            major = int(version.split(".")[0])
        except Exception:
            logger.warning("Could not read the queue Redis version; queue-mode models stay disabled until it can be")
            self._unsafe = "could not verify the queue Redis version (needs Redis 7 or newer)"
            return
        if major < 7:
            self._unsafe = f"queue-mode models need Redis 7 or newer (found {version})"
            logger.error(self._unsafe)
        else:
            self._unsafe = None
            self._verified = True

    @staticmethod
    def _check(name: str) -> None:
        if not _NAME_RE.fullmatch(name or ""):
            raise QueueAccessError(f"invalid model name: {name!r}")

    def _password(self, name: str) -> str:
        if not self._secret:
            raise QueueAccessError("queue-mode models need QUEUE_ACL_SECRET to be configured")
        digest = hmac.new(self._secret.encode(), f"queue-acl:{name}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _url(self, name: str, password: str) -> str:
        parts = urlsplit(self._base)
        host = parts.hostname or "localhost"
        netloc = f"m_{name}:{quote(password, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme or "redis", netloc, parts.path, "", ""))

    async def provision(self, name: str) -> str:
        """Creates or refreshes the ACL user and returns the Redis URL the model should use."""
        self._check(name)
        if not self._verified:
            await self.verify_server()  # retried on every attempt: a Redis that was still starting recovers on its own
        if self._unsafe or not self._verified:
            raise QueueAccessError(self._unsafe or "could not verify the queue Redis version")
        if self._legacy and not self._dedicated:
            raise QueueAccessError(
                "legacy queue-mode models (QUEUE_LEGACY_ACL) need a dedicated queue Redis: set QUEUE_REDIS_URL (they must not "
                "share the Redis that holds routes and API keys)"
            )
        password = self._password(name)
        try:
            await self._redis.execute_command(
                "ACL", "SETUSER", f"m_{name}", "reset", "on", f">{password}",
                f"~fastmlapi:{name}:*", "resetchannels", *commands_for(legacy=self._legacy),
            )
        except Exception as exc:
            logger.error("Could not create the Redis ACL user for %s: %s", name, type(exc).__name__)
            raise QueueAccessError(f"could not create the Redis ACL user: {type(exc).__name__}") from exc
        return self._url(name, password)

    async def remove(self, name: str) -> None:
        try:
            self._check(name)
            await self._redis.execute_command("ACL", "DELUSER", f"m_{name}")
        except Exception:
            logger.warning("Could not remove the Redis ACL user for %s", name)
