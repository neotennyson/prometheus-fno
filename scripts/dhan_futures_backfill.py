import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "dhan"
NIFTY_SID = "13"
FUT_SID = "68407"  # NIFTY Sep-2026 FUT (near month at build time; refresh from api-scrip-master.csv)


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


def candles(client, sid: str, segment: str, instrument: str, frm: date, to: date,
            interval: int = 0) -> pd.DataFrame:
    """interval 0 -> daily (/charts/historical); 1/5/15 -> minute (/charts/intraday).
    NOTE: chart responses are FLAT {open:[],timestamp:[]} — there is no 'data' wrapper."""
    body = {"securityId": sid, "exchangeSegment": segment, "instrument": instrument,
            "expiryCode": 0, "oi": False, "fromDate": str(frm), "toDate": str(to)}
    if interval > 0:
        body["interval"] = interval
    r = client._post("/charts/historical" if interval == 0 else "/charts/intraday", body)
    rows = r if "timestamp" in r else (r.get("data") or {})
    if not rows or not rows.get("timestamp"):
        return pd.DataFrame()
    return pd.DataFrame({"ts": rows["timestamp"], "open": rows["open"], "high": rows["high"],
                         "low": rows["low"], "close": rows["close"], "volume": rows.get("volume")})


def save_daily(df: pd.DataFrame, path: Path, tag: str):
    if df.empty:
        print(f"[dl] {tag}: EMPTY, skipped")
        return
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    df = df.drop_duplicates("dt").set_index("dt").sort_index().drop(columns="ts")
    df.to_parquet(path)
    print(f"[dl] {tag}: {len(df)} daily bars ({df.index.min()} -> {df.index.max()}) -> {path.name}")


def save_minute(frames: list, path: Path, tag: str):
    if not frames:
        print(f"[dl] {tag}: EMPTY, skipped")
        return
    m = pd.concat(frames)
    m["ts"] = pd.to_datetime(m["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    m = m.drop_duplicates("ts").sort_values("ts")
    m.to_parquet(path)
    print(f"[dl] {tag}: {len(m)} minute bars ({m['ts'].min()} -> {m['ts'].max()}) -> {path.name}")


def main():
    ap = argparse.ArgumentParser(description="Backfill Nifty INDEX + futures (daily + minute) for gap/overnight research")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    client = get_client()

    if args.probe:
        day = str(date.today() - timedelta(days=1))
        day2 = str(date.today())
        variants = [
            ("hist FUT contract-id", FUT_SID, "NSE_FNO", "FUTIDX"),
            ("hist INDEX", NIFTY_SID, "IDX_I", "INDEX"),
        ]
        for name, sid, seg, inst in variants:
            try:
                d = candles(client, sid, seg, inst, date.today() - timedelta(days=30), date.today())
                print(f"[probe] {name} daily: {len(d)} bars")
            except requests.HTTPError as e:
                print(f"[probe] {name} daily: HTTP {e.response.status_code} -> {e.response.text[:200]}")
            try:
                m = candles(client, sid, seg, inst, pd.Timestamp(day).date(), pd.Timestamp(day2).date(), interval=1)
                print(f"[probe] {name} 1min: {len(m)} bars")
            except requests.HTTPError as e:
                print(f"[probe] {name} 1min: HTTP {e.response.status_code} -> {e.response.text[:200]}")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start).date()

    # 90-day chunks (Dhan intraday poll limit)
    chunks, cur = [], start
    while cur < date.today():
        nxt = min(cur + timedelta(days=90), date.today())
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)

    def pull_min(sid, seg, inst, tag):
        frames = []
        for i, (a, b) in enumerate(chunks, 1):
            for attempt in range(3):
                try:
                    f = candles(client, sid, seg, inst, a, b, interval=1)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"[dl] {tag} chunk {i} ({a}->{b}) FAILED: {e}")
                        f = pd.DataFrame()
                    else:
                        time.sleep(5)
            if not f.empty:
                frames.append(f)
            time.sleep(1.2)
        save_minute(frames, OUT / f"nifty_{tag}_minute.parquet", f"{tag} 1min")

    # INDEX is the research-grade series (available back to inception)
    print(f"[dl] INDEX daily {start} -> {date.today()}")
    try:
        save_daily(candles(client, NIFTY_SID, "IDX_I", "INDEX", start, date.today()),
                   OUT / "nifty_index_daily.parquet", "index daily")
    except Exception as e:
        print(f"[dl] INDEX daily FAILED: {e}")
    pull_min(NIFTY_SID, "IDX_I", "INDEX", "index")

    # near-month future: only its own contract history (continuous series needs expired sids)
    print(f"[dl] FUT {FUT_SID} daily")
    try:
        save_daily(candles(client, FUT_SID, "NSE_FNO", "FUTIDX", start, date.today()),
                   OUT / "nifty_fut_daily.parquet", "fut daily")
    except Exception as e:
        print(f"[dl] FUT daily FAILED: {e}")


if __name__ == "__main__":
    main()
