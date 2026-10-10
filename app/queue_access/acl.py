import base64
import hashlib
import hmac
import logging
import re
from urllib.parse import quote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,38}[a-z0-9])?")

# STRICT (default): exactly the commands the current fastmlapi Redis backend issues
# (fastmlapi.queue.redis_backend.REDIS_COMMANDS_USED), and nothing else, not even harmless-looking ones. Whole command
# categories are never granted: they hide expensive or destructive commands (@keyspace has FLUSHALL, @string has LCS and
# SETRANGE, @set has SUNIONSTORE), and key patterns do not restrict commands that take no key. No Lua scripts and no SCAN
# means a model cannot hang the server with a script or list the names of other tenants' keys. Requires Redis >= 7 (checked
# at provision time, fail closed). If a new fastmlapi release needs another command, add it here one by one.
LIBRARY_COMMANDS = [
    "+lmove", "+lpush", "+rpush", "+lrem", "+lrange", "+llen", "+lpos",
    "+hset", "+hsetnx", "+hget", "+hmget", "+hgetall", "+hdel", "+hincrby",
    "+expire", "+sadd", "+srem", "+smembers", "+del", "+exists",
    "+watch", "+unwatch", "+multi", "+exec", "+discard", "+time", "+ping",
]
CONNECTION_COMMANDS = ["+hello", "+auth", "+client|setname", "+client|setinfo", "+client|id"]
MODEL_COMMANDS = ["-@all", *LIBRARY_COMMANDS, *CONNECTION_COMMANDS]

# LEGACY (QUEUE_LEGACY_ACL=true): OLDER fastmlapi versions used Lua scripts (register_script = EVALSHA + SCRIPT LOAD), SCAN and
# other string/sorted-set commands. Found by running the real library against Redis 7.4. Redis enforces the key prefix and the
# command list inside scripts, but SCAN is not filtered by key permissions and a script can hang the server, so the categories
# below are the price of running such a library. EVAL and SCRIPT FLUSH/KILL stay denied either way.
LEGACY_CATEGORIES = ["+@list", "+@hash", "+@string", "+@set", "+@sortedset"]
LEGACY_KEY_COMMANDS = [
    "+del", "+unlink", "+exists", "+expire", "+pexpire", "+expireat", "+pexpireat", "+persist", "+ttl", "+pttl", "+type",
]
LEGACY_COMMANDS = ["+evalsha", "+script|load", "+scan"]


def commands_for(*, legacy: bool) -> list[str]:
    if not legacy:
        return list(MODEL_COMMANDS)
    return [*MODEL_COMMANDS, *LEGACY_CATEGORIES, *LEGACY_KEY_COMMANDS, "+echo", *LEGACY_COMMANDS]


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
        if not self._dedicated:
            raise QueueAccessError(
                "queue-mode models need a dedicated queue Redis: set QUEUE_REDIS_URL. Redis cannot cap memory per user, so they must "
                "not share the Redis that holds routes and API keys"
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
