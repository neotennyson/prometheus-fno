from __future__ import annotations

import json

import numpy as np
import pandas as pd


def perf_stats(daily_pnl: pd.Series, capital: float) -> dict:
    pnl = daily_pnl.sort_index()
    if len(pnl) == 0:
        return {"days": 0}
    span_days = max((pnl.index[-1] - pnl.index[0]).days, 1)
    years = span_days / 365.25
    eq = capital + pnl.cumsum()
    cal_idx = pd.date_range(pnl.index[0], pnl.index[-1], freq="D")
    cal_pnl = pnl.reindex(cal_idx).fillna(0.0)
    rets = cal_pnl / capital
    total = float(eq.iloc[-1] - capital)
    cagr = ((eq.iloc[-1] / capital) ** (1 / years) - 1.0) if eq.iloc[-1] > 0 else -1.0
    sd = float(rets.std(ddof=0))
    sharpe = float(rets.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
    downside = rets[rets < 0]
    d_sd = float(downside.std(ddof=0)) if len(downside) > 1 else 0.0
    sortino = float(rets.mean() / d_sd * np.sqrt(252)) if d_sd > 0 else 0.0
    roll_max = eq.cummax()
    dd = (eq - roll_max) / roll_max
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    pf = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    return {
        "days": int(len(cal_pnl)),
        "trade_days": int(len(pnl)),
        "years": round(years, 2),
        "total_pnl": round(total, 0),
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_dd_pct": round(float(dd.min()) * 100, 2),
        "profit_factor": round(pf, 2),
        "win_rate_pct": round(float((pnl > 0).mean()) * 100, 2) if len(pnl) else 0.0,
        "best_day": round(float(pnl.max()), 0) if len(pnl) else 0,
        "worst_day": round(float(pnl.min()), 0) if len(pnl) else 0,
    }


def monthly_table(daily_pnl: pd.Series, capital: float) -> pd.DataFrame:
    pnl = daily_pnl.copy()
    m = pnl.groupby([pnl.index.year, pnl.index.month]).sum()
    out = {}
    for (y, mo), v in m.items():
        out.setdefault(y, {})[mo] = round(v, 0)
    return pd.DataFrame(out).T


def save_report(payload: dict, path: str):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
