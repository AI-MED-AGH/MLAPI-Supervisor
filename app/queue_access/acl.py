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
# Found by running the real fastmlapi queue against Redis 7.4: it needs TIME, Lua scripts (register_script = EVALSHA +
# SCRIPT LOAD), MULTI/EXEC pipelines and SCAN. On Redis >= 7 the key prefix and command list are enforced INSIDE scripts
# (tests/test_acl_real_redis.py proves it against a real server), so a model cannot read or write other keys.
# TWO LIMITS REMAIN, and both are why queue data must live on its own, dedicated Redis (provision() refuses otherwise):
#   * SCAN is NOT filtered by key permissions: a model can list the NAMES of every key in that Redis (never the values).
#   * A Lua script can hang the server for everyone, and after a write it cannot even be killed gently.
# EVAL and SCRIPT FLUSH/KILL stay denied. Drop "+scan" once fastmlapi stops using SCAN.
QUEUE_COMMANDS = ["+time", "+evalsha", "+script|load", "+multi", "+exec", "+scan"]
MODEL_COMMANDS = ["-@all", *DATA_CATEGORIES, *KEY_COMMANDS, *CONNECTION_COMMANDS, *QUEUE_COMMANDS]


class QueueAccessError(Exception):
    pass


class QueueAccess:
    """Per-model Redis ACL users: a model container can only touch `fastmlapi:<name>:*`."""

    def __init__(self, redis, *, secret: str, model_redis_url: str, dedicated: bool = True):
        self._redis = redis
        self._secret = secret
        self._base = model_redis_url
        self._unsafe: str | None = None
        self._verified = False
        self._dedicated = dedicated  # True only when the queue Redis holds nothing but queue data

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
                "queue-mode models need a dedicated queue Redis: set QUEUE_REDIS_URL (they must not share the Redis that "
                "holds routes and API keys)"
            )
        password = self._password(name)
        try:
            await self._redis.execute_command(
                "ACL", "SETUSER", f"m_{name}", "reset", "on", f">{password}",
                f"~fastmlapi:{name}:*", "resetchannels", *MODEL_COMMANDS,
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
