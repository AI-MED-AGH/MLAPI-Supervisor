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
MODEL_COMMANDS = ["-@all", *DATA_CATEGORIES, *KEY_COMMANDS, *CONNECTION_COMMANDS]


class QueueAccessError(Exception):
    pass


class QueueAccess:
    """Per-model Redis ACL users: a model container can only touch `fastmlapi:<name>:*`."""

    def __init__(self, redis, *, secret: str, model_redis_url: str):
        self._redis = redis
        self._secret = secret
        self._base = model_redis_url

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
