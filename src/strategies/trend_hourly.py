from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def generate_signals(hourly: pd.DataFrame, ema_fast: int = 9, ema_slow: int = 21,
                     adx_period: int = 14, adx_min: float = 18.0) -> pd.DataFrame:
    df = hourly.copy()
    df["ef"] = ema(df["close"], ema_fast)
    df["es"] = ema(df["close"], ema_slow)
    df["adx"] = adx(df, adx_period)
    raw = np.where((df["ef"] > df["es"]) & (df["adx"] >= adx_min), 1,
                   np.where((df["ef"] < df["es"]) & (df["adx"] >= adx_min), -1, 0))
    df["signal"] = pd.Series(raw, index=df.index).shift(1).fillna(0)
    return df


def simulate_trend(signals: pd.DataFrame, lot: int, fut_cost_fn, equity_fn,
                   risk_cfg: dict, square_off_time="15:15") -> list[dict]:
    trades = []
    pos_dir = 0
    entry_px = 0.0
    stop_px = 0.0
    entry_date = None
    lots = 0

    for ts, row in signals.iterrows():
        t = ts.time()
        if entry_date is not None and ts.date() != entry_date:
            pts = (row["open"] - entry_px) * pos_dir
            trades.append({"entry_ts": f"{entry_date}", "exit_ts": str(ts), "dir": pos_dir,
                           "pts": pts, "pnl": pts * lot * lots})
            pos_dir, lots, entry_date = 0, 0, None

        if pos_dir != 0 and t.strftime("%H:%M") >= square_off_time:
            pts = (row["close"] - entry_px) * pos_dir
            trades.append({"entry_ts": f"{entry_date}", "exit_ts": str(ts), "dir": pos_dir,
                           "pts": pts, "pnl": pts * lot * lots})
            pos_dir, lots, entry_date = 0, 0, None
            continue

        sig = int(row["signal"])
        if pos_dir == 0 and sig != 0 and t < (pd.Timestamp(square_off_time).time()):
            atr_est = max(abs(row["close"] - row["es"]), row["high"] - row["low"], 5.0)
            stop_dist = 2.0 * atr_est
            per_lot_risk = stop_dist * lot
            equity_now = equity_fn(ts)
            if per_lot_risk <= 0 or per_lot_risk > equity_now * 0.02:
                continue
            budget = equity_now * risk_cfg["risk_per_trade_pct"] / 100.0
            lots = max(1, int(budget // per_lot_risk))
            lots = min(lots, max(1, int(3 * (equity_now / 1_000_000.0))))
            if lots <= 0:
                continue
            cost = fut_cost_fn(row["close"], lots, lot)["total"]
            trades.append({"entry_ts": str(ts), "cost_only": True, "pnl": -cost})
            pos_dir = sig
            entry_px = row["close"]
            stop_px = entry_px - sig * stop_dist
            entry_date = ts.date()

    return trades
