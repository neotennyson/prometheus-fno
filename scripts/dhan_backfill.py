import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "dhan"


def get_client():
    from src.data.dhan_client import DhanClient
    tok = os.environ.get("DHAN_ACCESS_TOKEN")
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not tok or not cid:
        print("Set DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID first:")
        print('  export DHAN_ACCESS_TOKEN="..."')
        print('  export DHAN_CLIENT_ID="..."')
        sys.exit(1)
    return DhanClient(access_token=tok, client_id=cid)


def probe(client: DhanClient):
    print("[probe] expiry list for Nifty (securityId 13):")
    try:
        expiries = client.expiry_list("13")
        print("  ", expiries[:6])
    except Exception as e:
        print("   FAILED:", e)
    print("[probe] rolling expired options — one sample call (WEEK, expiryCode=1):")
    frm = str(date.today() - timedelta(days=45))
    to = str(date.today() - timedelta(days=15))
    for flag, code in (("WEEK", 1), ("MONTH", 1)):
        try:
            r = client.rolling_option(13, "CALL", "ATM", frm, to, expiry_flag=flag, expiry_code=code)
            d = r.get("data") or {}
            ce = d.get("ce") or {}
            ts = ce.get("timestamp") or []
            print(f"  [{flag} code={code}] OK — {len(ts)} rows")
            if ts:
                strikes = ce.get("strike") or []
                spots = ce.get("spot") or []
                uniq = sorted(set(strikes))
                print(f"    ts range: {ts[0]} .. {ts[-1]}")
                print(f"    distinct strikes: {len(uniq)} (sample {uniq[:5]})")
                print(f"    spot sample: {spots[:3]}")
                print(f"    iv sample: {(ce.get('iv') or [None])[:3]}")
        except Exception as e:
            print(f"  [{flag} code={code}] FAILED: {e}")
    print("  (strikes changing over time = rolling works; rows clustering on expiry days = correct)")


def month_chunks(start: date, end: date):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=30), end)
        yield cur, nxt
        cur = nxt


def backfill_rolling_options(client: DhanClient, years_back: int = 5,
                             strikes=("ATM", "ATM+3", "ATM-3")):
    OUT.mkdir(parents=True, exist_ok=True)
    end = date.today()
    start = end - timedelta(days=365 * years_back)
    combos = []
    for s in strikes:
        if s == "ATM" or s.startswith("ATM+"):
            combos.append(("CALL", s))
        if s == "ATM" or s.startswith("ATM-"):
            combos.append(("PUT", s))
    total = 0
    for opt_type, strike in combos:
        frames = []
        tag = f"{opt_type}_{strike.replace('+','p').replace('-','m')}"
        out_file = OUT / f"nifty_opt_{tag}.parquet"
        if out_file.exists():
            print(f"skip {tag} (exists)")
            continue
        for frm, to in month_chunks(start, end):
            for attempt in range(3):
                try:
                    r = client.rolling_option(
                        13, opt_type, strike, str(frm), str(to),
                        expiry_flag="WEEK", expiry_code=1,
                    )
                    side = r.get("data", {}).get("ce") if opt_type == "CALL" else r.get("data", {}).get("pe")
                    if side and side.get("timestamp"):
                        df = pd.DataFrame({k: v for k, v in side.items() if k != "iv"})
                        iv = side.get("iv")
                        if iv and len(iv) == len(df):
                            df["iv"] = iv
                        frames.append(df)
                    break
                except Exception as e:
                    wait = 5 * (attempt + 1)
                    print(f"  retry {tag} {frm}->{to} after {wait}s ({e})")
                    time.sleep(wait)
            time.sleep(3.2)
        if frames:
            alldf = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp")
            alldf["ts"] = pd.to_datetime(alldf["timestamp"], unit="s")
            alldf.to_parquet(out_file)
            total += 1
            print(f"{tag}: {len(alldf)} rows -> {out_file.name}")
        else:
            print(f"{tag}: EMPTY")
    print(f"\nDone. {total} files in {OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="test API shape with one call")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--strikes", default="ATM,ATM+3,ATM-3",
                    help="comma list, e.g. ATM+2,ATM-2,ATM+4,ATM-4 (ATM always implied for wings 100/200)")
    args = ap.parse_args()
    client = get_client()
    if args.probe:
        probe(client)
    else:
        backfill_rolling_options(client, args.years, strikes=tuple(s.strip() for s in args.strikes.split(",")))


if __name__ == "__main__":
    main()
