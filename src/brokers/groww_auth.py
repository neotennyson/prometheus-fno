"""Groww authentication: RFC-6238 TOTP generation + official token exchange.

Flow (Groww Trading API docs, groww.in/trade-api/docs):
  POST https://api.groww.in/v1/token/api/access
  Authorization: Bearer <TOTP_TOKEN aka API key>
  Body: {"key_type": "totp", "totp": "<6-digit code>"}
  -> {"token": "...", "expiry": "...", ...}

Groww access tokens expire DAILY at 06:00 IST, so this is run by launchd at
08:45 before the trading session. The token endpoint is limited to 150 calls
per 24h -- renew once a day, never in a loop.

The TOTP implementation is RFC 6238 (SHA-1, 30 s, 6 digits) written in-house
against the published test vectors -- no extra dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
import time
from pathlib import Path

import httpx

GROWW_TOKEN_URL = "https://api.groww.in/v1/token/api/access"


def totp_now(secret_b32: str, at: float | None = None,
             digits: int = 6, period: int = 30) -> str:
    """RFC 6238 TOTP. `secret_b32` is the base32 seed from the authenticator
    setup (spaces/stripped case tolerated)."""
    if at is None:
        at = time.time()
    cleaned = secret_b32.strip().replace(" ", "").upper()
    pad = "=" * (-len(cleaned) % 8)
    key = base64.b32decode(cleaned + pad)
    counter = int(at // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def generate_access_token(totp_token: str, totp_secret: str,
                          timeout: float = 12.0,
                          base_url: str = GROWW_TOKEN_URL) -> dict:
    """Exchange API key (TOTP token) + live TOTP code for an access token.
    Retries once with a fresh code if the 30 s window flips mid-request.
    Returns the parsed JSON body; raises RuntimeError on non-200."""
    body = {"key_type": "totp", "totp": totp_now(totp_secret)}
    headers = {"Authorization": f"Bearer {totp_token}",
               "Content-Type": "application/json"}
    last = None
    for _ in range(2):                       # handle window-boundary rotation
        r = httpx.post(base_url, json=body, headers=headers, timeout=timeout)
        last = r
        if r.status_code == 200:
            return r.json()
        time.sleep(1)
    raise RuntimeError(f"Groww token exchange failed: HTTP {last.status_code} "
                       f"{last.text[:200]}")


def write_env_token(env_path: Path, key: str, value: str) -> bool:
    """Set KEY=value in a .env file, replacing the existing line if present.
    Returns True if the file changed. Never logs the value."""
    env_path = Path(env_path)
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    new_text = "\n".join(out) + "\n"
    if env_path.exists() and env_path.read_text() == new_text:
        return False
    env_path.write_text(new_text)
    return True
