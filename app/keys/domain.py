import base64
import hashlib
import re
import secrets
import string

_KEY_RE = re.compile(r"^mlapi_([A-Za-z0-9]{12})_([A-Za-z0-9_-]{43})$")
_ID_ALPHABET = string.ascii_letters + string.digits


def generate_key() -> tuple[str, str, str]:
    """Returns (key_id, secret, raw_key). The raw key is shown to the client once."""
    key_id = "".join(secrets.choice(_ID_ALPHABET) for _ in range(12))
    secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    return key_id, secret, f"mlapi_{key_id}_{secret}"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def parse_key(raw: str) -> tuple[str, str] | None:
    match = _KEY_RE.match(raw or "")
    return (match.group(1), match.group(2)) if match else None
