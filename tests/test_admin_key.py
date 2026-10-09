import hashlib
import re

from app.admin_key import generate_admin_key, main
from app.auth import hash_admin_key


def test_generated_key_and_hash_are_consistent():
    raw, hashed = generate_admin_key()
    assert len(raw) >= 40 and re.fullmatch(r"[A-Za-z0-9_-]+", raw)
    assert hashed == hash_admin_key(raw) == hashlib.sha256(raw.encode()).hexdigest()


def test_keys_are_unique():
    assert len({generate_admin_key()[0] for _ in range(20)}) == 20


def test_cli_prints_key_and_hash(capsys):
    main([])
    out = capsys.readouterr().out
    assert "ADMIN_API_KEYS=" in out and "X-Admin-Key" in out
    raw = re.search(r"X-Admin-Key: (\S+)", out).group(1)
    assert f"ADMIN_API_KEYS={hash_admin_key(raw)}" in out
