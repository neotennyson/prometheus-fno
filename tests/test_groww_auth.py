import pytest

from src.brokers.groww_auth import totp_now, write_env_token


def test_totp_rfc6238_vectors():
    """Official RFC 6238 SHA-1 test vectors, seed '12345678901234567890'."""
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_now(seed, at=59) == "287082"
    assert totp_now(seed, at=1111111109) == "081804"
    assert totp_now(seed, at=1234567890) == "005924"


def test_totp_tolerates_spacing_and_case():
    seed = "gezd gnbv gy3t qojq gezd gnbv gy3t qojq"
    assert totp_now(seed, at=59) == totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59)


def test_totp_window_consistency():
    seed = "JBSWY3DPEHPK3PXP"
    a = totp_now(seed, at=1_000_000)
    b = totp_now(seed, at=1_000_019)           # same 30s window (999990–1000019)
    assert a == b
    assert len(a) == 6


def test_write_env_token_replaces_and_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nGROWW_ACCESS_TOKEN=old\nB=2\n")
    assert write_env_token(env, "GROWW_ACCESS_TOKEN", "new") is True
    text = env.read_text()
    assert "GROWW_ACCESS_TOKEN=new" in text and "old" not in text and "A=1" in text
    assert write_env_token(env, "GROWW_ACCESS_TOKEN", "new") is False   # idempotent
    assert write_env_token(env, "NEW_KEY", "v") is True
    assert "NEW_KEY=v" in env.read_text()
