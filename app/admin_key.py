"""Generates an admin key and the value to put in ADMIN_API_KEYS:  python -m app.admin_key"""
import secrets

from app.auth import hash_admin_key


def generate_admin_key() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_admin_key(raw)


def main(argv: list[str] | None = None) -> None:
    raw, hashed = generate_admin_key()
    print("Keep this key secret. It is shown once and only its hash is stored.\n")
    print(f"  Admin key (send as header):  X-Admin-Key: {raw}")
    print(f"  Put this in your .env:        ADMIN_API_KEYS={hashed}")


if __name__ == "__main__":
    main()
