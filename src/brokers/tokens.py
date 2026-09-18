"""Broker token access via the user's private token API.

    Base: https://tokens.example.invalid
    Auth: X-API-Key header (value of TOKEN_API_KEY from .env)
    GET  /token/dhan | /token/groww | /token/all -> {"token": "...", "expiry": "..."}
    POST /refresh  -> forces server-side regeneration (only on 401/403/expiry)

Contract:
  get_token("dhan" | "groww") -> str
  refresh_and_get(broker)     -> str   (POST /refresh, wait 5 s, refetch)

Cache: ~/.broker_tokens.json, chmod 600, entries reused when < 6 h old.
Stdlib only (urllib.request, json, os, time). Tokens are never printed,
logged, or committed; failures reference <token>, never the value.
"""
from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("TOKEN_API_BASE_URL", "https://tokens.example.invalid")
CACHE_PATH = Path(os.environ.get(
    "BROKER_TOKEN_CACHE", str(Path.home() / ".broker_tokens.json")))
CACHE_TTL_SECONDS = 6 * 3600
_VALID_BROKERS = ("dhan", "groww")

# legacy fallbacks when TOKEN_API_KEY is not configured (manual .env tokens)
_LEGACY_ENV = {"dhan": "DHAN_ACCESS_TOKEN", "groww": "GROWW_ACCESS_TOKEN"}


def _api_key() -> str:
    key = os.environ.get("TOKEN_API_KEY", "")
    if not key:
        raise RuntimeError("TOKEN_API_KEY not set (add it to .env)")
    return key


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache))
        os.chmod(CACHE_PATH, stat.S_IRUSR | stat.S_IWUSR)   # 600
    except Exception:
        pass                                                # cache is best-effort


def _request(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(
        f"{API_BASE}{path}", method=method,
        headers={"X-API-Key": _api_key(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:200]
        raise RuntimeError(f"token API HTTP {exc.code} for {path}: {body}") from None


def get_token(broker: str, ttl: int | None = None) -> str:
    """Point-of-use token getter for any broker client. Raises RuntimeError
    (never returns an empty string) when no source is configured."""
    broker = broker.lower()
    if broker not in _VALID_BROKERS:
        raise ValueError(f"unknown broker {broker!r}; expected {(_VALID_BROKERS,)}")
    if not os.environ.get("TOKEN_API_KEY"):
        legacy = os.environ.get(_LEGACY_ENV[broker], "")
        if legacy:
            return legacy                                   # manual token mode
        raise RuntimeError(
            f"no token source for '{broker}': TOKEN_API_KEY unset and "
            f"{_LEGACY_ENV[broker]} empty in .env")

    cache = _load_cache()
    entry = cache.get(broker) or {}
    age = time.time() - float(entry.get("fetched_at", 0))
    if entry.get("token") and age < (CACHE_TTL_SECONDS if ttl is None else ttl):
        return entry["token"]

    data = _request(f"/token/{broker}")
    token = data.get("token") or data.get("access_token") or ""
    if not token:
        raise RuntimeError(f"token API returned no token for {broker}")
    cache[broker] = {"token": token, "fetched_at": time.time(),
                     "expiry": str(data.get("expiry", ""))}
    _save_cache(cache)
    return token


def refresh_and_get(broker: str) -> str:
    """401/403/token-expired recovery: force regeneration server-side,
    wait 5 s, then refetch. Callers retry their request once afterwards."""
    broker = broker.lower()
    try:
        _request("/refresh", method="POST")
    except RuntimeError:
        pass                        # refresh endpoint may throttle; still refetch
    time.sleep(5)
    cache = _load_cache()
    cache.pop(broker, None)
    _save_cache(cache)
    return get_token(broker, ttl=0)
