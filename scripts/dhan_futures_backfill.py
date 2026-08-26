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
NIFTY_SID = "13"


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


def hist_chunk(client, frm: date, to: date, interval: int) -> pd.DataFrame:
    body = {"securityId": NIFTY_SID, "exchangeSegment": "NSE_FNO", "instrument": "FUTIDX",
            "expiryCode": 0, "oi": False, "fromDate": str(frm), "toDate": str(to),
            "interval": interval}
    r = client.post("/charts/intraday" if interval > 0 else "/charts/historical", json=body)
    rows = r.get("data", {})
    if not rows or not rows.get("timestamp"):
        return pd.DataFrame()
    df = pd.DataFrame({"ts": rows["timestamp"], "open": rows["open"], "high": rows["high"],
                       "low": rows["low"], "close": rows["close"], "volume": rows.get("volume")})
    return df


def main():
    ap = argparse.ArgumentParser(description="Backfill Nifty futures (daily + minute) for gap/overnight research")
    ap.add_argument("--start", default="2021-08-24")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    client = get_client()

    if args.probe:
        r = client.post("/charts/historical", json={
            "securityId": NIFTY_SID, "exchangeSegment": "NSE_FNO", "instrument": "FUTIDX",
            "expiryCode": 0, "oi": False, "fromDate": str(date.today() - timedelta(days=30)),
            "toDate": str(date.today() - timedelta(days=25)), "interval": 15})
        print("[probe] FUTIDX daily sample:", str(r)[:300])
        return

    OUT.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start).date()

    # daily candles (one call for the full range)
    print(f"[fut] daily {start} -> {date.today()}")
    d = hist_chunk(client, start, date.today(), interval=0)
    if not d.empty:
        d["dt"] = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
        d = d.drop_duplicates("dt").set_index("dt").sort_index().drop(columns="ts")
        d.to_parquet(OUT / "nifty_fut_daily.parquet")
        print(f"[fut] saved {len(d)} daily bars -> nifty_fut_daily.parquet")

    # minute candles, 90-day chunks (Dhan intraday limit)
    chunks, cur = [], start
    while cur < date.today():
        nxt = min(cur + timedelta(days=90), date.today())
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    frames = []
    for i, (a, b) in enumerate(chunks, 1):
        for attempt in range(3):
            try:
                f = hist_chunk(client, a, b, interval=1)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[fut] chunk {i} ({a}->{b}) FAILED: {e}")
                    f = pd.DataFrame()
                else:
                    time.sleep(5)
        if not f.empty:
            frames.append(f)
            print(f"[fut] chunk {i}/{len(chunks)} {a}->{b}: {len(f)} bars")
        time.sleep(1.2)
    if frames:
        m = pd.concat(frames)
        m["ts"] = pd.to_datetime(m["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        m = m.drop_duplicates("ts").sort_values("ts")
        m.to_parquet(OUT / "nifty_fut_minute.parquet")
        print(f"[fut] saved {len(m)} minute bars ({m['ts'].min()} -> {m['ts'].max()}) -> nifty_fut_minute.parquet")


if __name__ == "__main__":
    main()
