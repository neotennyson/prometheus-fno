"""ATLAS data layer: local dailies + cached global proxies.

Global overnight parameters (SPX, Nasdaq, USDINR, crude, US10Y) are fetched via
yfinance into data/raw/global_<sym>.parquet and refreshed on demand. The 24/7
monitor service uses the same cache so backtests and live see identical fields.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data.intraday import repo_root

GLOBAL_SYMBOLS = {
    "spx": "^GSPC",
    "nasdaq": "^IXIC",
    "usdinr": "INR=X",
    "crude": "CL=F",
    "us10y": "^TNX",
}


def raw_dir() -> Path:
    return repo_root() / "data" / "raw"


def load_daily(name: str) -> pd.DataFrame:
    df = pd.read_parquet(raw_dir() / f"{name}_daily.parquet")
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def refresh_global(sym_key: str, period: str = "12y") -> pd.DataFrame | None:
    import yfinance as yf
    sym = GLOBAL_SYMBOLS[sym_key]
    try:
        df = yf.download(sym, period=period, interval="1d",
                         progress=False, auto_adjust=True)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].copy()
    df.columns = [c.lower() for c in df.columns]
    df.index.name = None
    p = raw_dir() / f"global_{sym_key}.parquet"
    df.to_parquet(p)
    return df


def load_global(sym_key: str, refresh: bool = False) -> pd.DataFrame | None:
    p = raw_dir() / f"global_{sym_key}.parquet"
    if refresh or not p.exists():
        df = refresh_global(sym_key)
        if df is not None:
            return df
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def spx_overnight_map(nifty_index: pd.DatetimeIndex,
                      spx: pd.DataFrame | None) -> pd.Series:
    """For each NSE session date, the return of the most recent COMPLETED US
    session before that NSE open. NaN when no prior US close exists."""
    if spx is None or len(spx) < 3:
        return pd.Series(np.nan, index=nifty_index)
    r = spx["close"].pct_change()
    out = {}
    us_dates = spx.index.normalize()
    import numpy as np
    pos = np.searchsorted(us_dates.values,
                          np.array([d.date() for d in nifty_index], dtype="datetime64[D]"),
                          side="left")
    for d, pi in zip(nifty_index, pos):
        if pi >= 1 and us_dates[pi - 1] < pd.Timestamp(d.date()):
            out[d] = float(r.iloc[pi - 1])
    return pd.Series(out)


import numpy as np  # noqa: E402  (used above in helper)
