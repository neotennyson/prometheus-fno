#!/usr/bin/env python
"""Renew broker access tokens before the trading session.

  .venv/bin/python scripts/token_renew.py            # all configured brokers
  .venv/bin/python scripts/token_renew.py --broker groww

Groww: official TOTP flow (see src/brokers/groww_auth.py). Needs in .env:
  GROWW_TOTP_TOKEN=<API key from groww.in/trade-api/api-keys>
  GROWW_TOTP_SECRET=<base32 seed>
Writes GROWW_ACCESS_TOKEN back into .env; valid until 06:00 IST next day.

Dhan: OFFICIAL TOTP flow (docs: dhanhq.co/docs/v2/authentication):
  POST auth.dhan.co/app/generateAccessToken?dhanClientId=&pin=&totp=
Needs in .env: DHAN_CLIENT_ID + DHAN_PIN (6-digit login PIN) + DHAN_TOTP_SECRET
(the base32 seed shown during 'Setup TOTP' on web.dhan.co). Writes a fresh
24h DHAN_ACCESS_TOKEN into .env. Falls back to manual token if TOTP unset.
Exit code 0 = all good for the session, 1 = at least one broker not ready.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brokers.groww_auth import generate_access_token, totp_now, write_env_token
from src.config import ROOT, load_dotenv

ENV_PATH = ROOT / ".env"
DHAN_TOKEN_URL = "https://auth.dhan.co/app/generateAccessToken"


def renew_groww() -> bool:
    key = os.environ.get("GROWW_TOTP_TOKEN") or os.environ.get("GROWW_API_KEY", "")
    secret = os.environ.get("GROWW_TOTP_SECRET", "")
    if not key or not secret:
        print("[groww] SKIP — GROWW_TOTP_TOKEN / GROWW_TOTP_SECRET not set in .env")
        return False
    try:
        resp = generate_access_token(key, secret)
        token = resp.get("token") or resp.get("access_token")
        if not token:
            print(f"[groww] FAIL — unexpected response keys: {list(resp)}")
            return False
        changed = write_env_token(ENV_PATH, "GROWW_ACCESS_TOKEN", token)
        expiry = resp.get("expiry", "06:00 IST (daily)")
        print(f"[groww] OK — access token {'written to .env' if changed else 'unchanged'} "
              f"· expires {expiry}")
        return True
    except Exception as exc:
        print(f"[groww] FAIL — {exc}")
        return False


def renew_dhan() -> bool:
    """Official Dhan TOTP flow (dhanhq.co/docs/v2/authentication):
    POST client_id + pin + fresh TOTP -> 24h access token, written to .env.
    Needs DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET in .env."""
    import httpx
    cid = os.environ.get("DHAN_CLIENT_ID", "")
    pin = os.environ.get("DHAN_PIN", "")
    secret = os.environ.get("DHAN_TOTP_SECRET", "")
    tok = os.environ.get("DHAN_ACCESS_TOKEN", "")
    if cid and pin and secret:
        try:
            code = totp_now(secret)
            r = httpx.post(DHAN_TOKEN_URL, params={
                "dhanClientId": cid, "pin": pin, "totp": code}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                token = data.get("accessToken", "")
                if token:
                    write_env_token(ENV_PATH, "DHAN_ACCESS_TOKEN", token)
                    print(f"[dhan] OK — fresh 24h token written "
                          f"(expires {data.get('expiryTime', '?')[:16]})")
                    return True
            # window-boundary retry with a fresh code
            code2 = totp_now(secret, at=__import__("time").time() + 30)
            r = httpx.post(DHAN_TOKEN_URL, params={
                "dhanClientId": cid, "pin": pin, "totp": code2}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                token = data.get("accessToken", "")
                if token:
                    write_env_token(ENV_PATH, "DHAN_ACCESS_TOKEN", token)
                    print(f"[dhan] OK — fresh 24h token written "
                          f"(expires {data.get('expiryTime', '?')[:16]})")
                    return True
            print(f"[dhan] FAIL — HTTP {r.status_code}: {r.text[:150]}")
            return False
        except Exception as exc:
            print(f"[dhan] FAIL — {exc}")
            return False
    if tok:
        print(f"[dhan] present ({len(tok)} chars) — set DHAN_PIN + DHAN_TOTP_SECRET "
              f"in .env for daily auto-renewal")
        return True
    print("[dhan] NOT READY — paste DHAN_ACCESS_TOKEN, or set DHAN_CLIENT_ID + "
          "DHAN_PIN + DHAN_TOTP_SECRET for auto-renewal")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", choices=["groww", "dhan", "all"], default="all")
    args = ap.parse_args()
    load_dotenv()

    results = []
    if args.broker in ("groww", "all"):
        results.append(renew_groww())
    if args.broker in ("dhan", "all"):
        results.append(renew_dhan())

    print(f"[token-renew] finished {datetime.now(IST_TZ()).strftime('%d %b %H:%M %Z')}")
    return 0 if all(results) else 1


def IST_TZ():
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Kolkata")


if __name__ == "__main__":
    sys.exit(main())
