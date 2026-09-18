import json
import os
import stat
import time

import pytest

import src.brokers.tokens as T


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "CACHE_PATH", tmp_path / "cache.json")
    yield


def test_get_token_falls_back_to_legacy_env(monkeypatch):
    monkeypatch.delenv("TOKEN_API_KEY", raising=False)
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "legacy-token")
    assert T.get_token("dhan") == "legacy-token"


def test_get_token_raises_when_no_source(monkeypatch):
    monkeypatch.delenv("TOKEN_API_KEY", raising=False)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        T.get_token("dhan")


def test_cache_reuse_within_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_req(path, method="GET"):
        calls["n"] += 1
        return {"token": f"tok-{calls['n']}", "expiry": "x"}

    monkeypatch.setattr(T, "_request", fake_req)
    monkeypatch.setenv("TOKEN_API_KEY", "k")
    a = T.get_token("dhan")
    b = T.get_token("dhan")              # within TTL -> cache hit
    assert a == b == "tok-1" and calls["n"] == 1
    # age the entry beyond TTL -> refetch
    cache = T._load_cache()
    cache["dhan"]["fetched_at"] = time.time() - T.CACHE_TTL_SECONDS - 1
    T._save_cache(cache)
    c = T.get_token("dhan")
    assert c == "tok-2" and calls["n"] == 2


def test_cache_file_is_chmod_600(monkeypatch):
    monkeypatch.setenv("TOKEN_API_KEY", "k")
    monkeypatch.setattr(T, "_request", lambda p, method="GET": {"token": "t"})
    T.get_token("dhan")
    mode = stat.S_IMODE(os.stat(T.CACHE_PATH).st_mode)
    assert mode == 0o600


def test_refresh_and_get_clears_cache_and_refetches(monkeypatch):
    calls = {"n": 0}
    posted = {"n": 0}

    def fake_req(path, method="GET"):
        if method == "POST":
            posted["n"] += 1
            return {}
        calls["n"] += 1
        return {"token": f"fresh-{calls['n']}"}

    monkeypatch.setattr(T, "_request", fake_req)
    monkeypatch.setattr(T, "time", time)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    monkeypatch.setenv("TOKEN_API_KEY", "k")
    T.get_token("dhan")                              # warm cache with tok fresh-1
    out = T.refresh_and_get("dhan")
    assert out == "fresh-2" and posted["n"] == 1 and calls["n"] == 2


def test_unknown_broker_rejected():
    with pytest.raises(ValueError):
        T.get_token("zerodha")
