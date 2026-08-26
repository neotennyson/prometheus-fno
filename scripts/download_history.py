import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.data.ingest_yf import _chart, _save

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
CORE = [("nifty", "^NSEI"), ("indiavix", "^INDIAVIX"), ("banknifty", "^NSEBANK"), ("sensex", "^BSESN")]


def main():
    for name, sym in CORE:
        out = RAW / f"{name}_daily.parquet"
        if out.exists():
            print(f"skip {name} (exists)", flush=True)
            continue
        t0 = time.time()
        df = _chart(sym, "1d",
                    period1=int(pd.Timestamp("2014-01-01").timestamp()),
                    period2=int(pd.Timestamp("2026-12-31").timestamp()))
        _save(df[["open", "high", "low", "close"]], name, "daily", RAW)
        print(f"{name}: {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()} ({time.time()-t0:.0f}s)", flush=True)
        time.sleep(5)

    hourly = RAW / "nifty_hourly.parquet"
    if not hourly.exists():
        df = _chart("^NSEI", "60m",
                    period1=int((pd.Timestamp.utcnow() - pd.Timedelta(days=730)).timestamp()),
                    period2=int(pd.Timestamp.now().timestamp()))
        df = df[["open", "high", "low", "close"]]
        sessions = df.index.normalize().nunique()
        print(f"nifty hourly: {len(df)} bars / {sessions} sessions", flush=True)
        _save(df, "nifty", "hourly", RAW)

    print("DOWNLOAD_DONE", flush=True)


if __name__ == "__main__":
    main()
