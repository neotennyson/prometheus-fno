#!/usr/bin/env python
"""Renew broker access tokens before the trading session.

  .venv/bin/python scripts/token_renew.py            # all configured brokers
  .venv/bin/python scripts/token_renew.py --broker groww

Groww: official TOTP flow (see src/brokers/groww_auth.py). Needs in .env:
  GROWW_TOTP_TOKEN=<API key from groww.in/trade-api/api-keys>
  GROWW_TOTP_SECRET=<base32 seed>
Writes GROWW_ACCESS_TOKEN back into .env; valid until 06:00 IST next day.

Dhan: NO official programmatic token API exists (their TOTP is app-login 2FA,
not API auth) -- paste DHAN_ACCESS_TOKEN from web.dhan.co manually when it
expires. This script reports if it is missing/expired but cannot renew it.
Exit code 0 = all good for the session, 1 = at least one broker not ready.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brokers.groww_auth import generate_access_token, write_env_token
from src.config import ROOT, load_dotenv

ENV_PATH = ROOT / ".env"


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


def check_dhan() -> bool:
    tok = os.environ.get("DHAN_ACCESS_TOKEN", "")
    if not tok:
        print("[dhan] NOT READY — paste DHAN_ACCESS_TOKEN into .env "
              "(web.dhan.co → Profile → DhanHQ Trading APIs → Generate Token)")
        return False
    print(f"[dhan] present ({len(tok)} chars) — validity checked at session start")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", choices=["groww", "dhan", "all"], default="all")
    args = ap.parse_args()
    load_dotenv()

    results = []
    if args.broker in ("groww", "all"):
        results.append(renew_groww())
    if args.broker in ("dhan", "all"):
        results.append(check_dhan())

    print(f"[token-renew] finished {datetime.now(IST_TZ()).strftime('%d %b %H:%M %Z')}")
    return 0 if all(results) else 1


def IST_TZ():
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Kolkata")


if __name__ == "__main__":
    sys.exit(main())
