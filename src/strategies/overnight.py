from __future__ import annotations

import numpy as np
import pandas as pd


def backtest_overnight(daily: pd.DataFrame, params: dict, lot: int,
                       fut_cost_fn, equity_start: float, min_equity: float,
                       vix: pd.Series | None = None) -> tuple[pd.Series, dict]:
    adverse = float(params.get("adverse_open_exit_pct", 1.5))
    vix_gate = float(params.get("vix_min_gate", 0.0)) if params.get("vix_min_gate") else 0.0
    close = daily["close"]
    open_ = daily["open"]

    rows: dict = {}
    eq = equity_start
    n_trades = 0
    gross_bp_sum = 0.0
    stops = 0
    tradeable = equity_start >= min_equity
    vix_prev = vix.reindex(daily.index).ffill().shift(1) if vix is not None else None

    for i in range(1, len(daily)):
        dt = daily.index[i]
        pc = float(close.iloc[i - 1])
        op = float(open_.iloc[i])
        if pc <= 0:
            continue
        if tradeable and vix_prev is not None and vix_gate > 0:
            vv = float(vix_prev.iloc[i]) if np.isfinite(float(vix_prev.iloc[i])) else None
            if vv is None or vv < vix_gate:
                continue
        raw_pts = op - pc
        stop_pts = pc * adverse / 100.0
        eff_pts = max(raw_pts, -stop_pts)
        if eff_pts < raw_pts:
            stops += 1
        lots = 1 if tradeable else 0
        if lots == 0:
            continue
        cost = fut_cost_fn(op, lots, lot)["total"]
        pnl = eff_pts * lot * lots - cost
        rows[dt] = pnl
        eq += pnl
        n_trades += 1
        gross_bp_sum += raw_pts / pc * 10000.0

    s = pd.Series(rows).sort_index()
    info = {
        "trades": n_trades,
        "final_equity": round(eq, 0),
        "avg_gross_bp": round(gross_bp_sum / max(n_trades, 1), 2),
        "stop_days": stops,
        "capital_gated": bool(equity_start < min_equity),
    }
    return s, info
