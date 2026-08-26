from __future__ import annotations

import numpy as np
import pandas as pd


def atr_daily(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def generate_gap_trades(daily: pd.DataFrame, vix: pd.Series | None, params: dict,
                        lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    atr_p = int(params.get("atr_period", 14))
    fade_thr = float(params.get("fade_gap_atr", 0.75))
    extreme_thr = float(params.get("extreme_gap_atr", 1.2))
    stop_mult = float(params.get("stop_atr_mult", 1.0))
    risk_pct = float(params.get("risk_pct", 0.5))

    d = daily.copy()
    d["atr"] = atr_daily(d, atr_p)
    d["gap"] = d["open"] / d["close"].shift(1) - 1.0
    d["gap_atr"] = d["gap"] / (d["atr"] / d["close"].shift(1)).replace(0, np.nan)

    trades = []
    for i in range(max(atr_p + 2, 20), len(d)):
        row = d.iloc[i]
        prev_close = d.iloc[i - 1]["close"]
        g_atr = row["gap_atr"]
        equity = equity_fn(d.index[i])
        if not np.isfinite(g_atr) or g_atr == 0 or equity <= 0 or prev_close <= 0:
            continue
        if abs(g_atr) < fade_thr:
            continue

        direction = -int(np.sign(g_atr))
        entry = row["open"]
        stop_dist = stop_mult * row["atr"] * max(1.0, abs(g_atr) * 0.75)
        target = prev_close

        per_lot_risk = stop_dist * lot
        if per_lot_risk <= 0 or per_lot_risk > equity * 0.02:
            continue
        budget = equity * risk_pct / 100.0
        lots = max(1, int(budget // per_lot_risk))
        lots = min(lots, max(1, int(3 * (equity / 1_000_000.0))))
        if lots <= 0:
            continue

        if direction > 0:
            stop_px = entry - stop_dist
            hit_stop_first = row["low"] <= stop_px
            hit_target = (not hit_stop_first) and row["high"] >= target
        else:
            stop_px = entry + stop_dist
            hit_stop_first = row["high"] >= stop_px
            hit_target = (not hit_stop_first) and row["low"] <= target

        if direction > 0:
            both_possible = row["low"] <= stop_px and row["high"] >= target
        else:
            both_possible = row["high"] >= stop_px and row["low"] <= target
        if both_possible:
            hit_stop_first = True

        exit_px = stop_px if hit_stop_first else (target if hit_target else row["close"])
        pts = (exit_px - entry) * direction
        cost = fut_cost_fn(entry, lots, lot)["total"]
        trades.append({
            "date": str(d.index[i].date()),
            "dir": direction,
            "entry": entry,
            "exit": exit_px,
            "pts": pts,
            "lots": lots,
            "cost": cost,
            "pnl": pts * lot * lots - cost,
            "stopped": bool(hit_stop_first),
            "gap_atr": float(g_atr),
        })
    return trades
