"""Shared execution semantics for intraday futures-style strategies on 1-min spot.

Conventions (must stay conservative):
- Signals act on a bar CLOSE; entry price is that same close (slippage carried by
  CostModel.futures_round_trip, 1.5 pts/side).
- Intrabar extremes are proxies max/min(open, close) of minute closes. When both a
  stop and a target are touchable in the same bar, the STOP fills first.
- One open position per strategy per day; all positions flat by exit time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def tmin(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def bar_minutes(day: pd.DataFrame) -> np.ndarray:
    idx = day.index
    return (idx.hour * 60 + idx.minute).to_numpy()


def size_lots(equity: float, stop_dist_pts: float, lot: int, risk_cfg: dict) -> int:
    """Same sizing convention as gap_reversion: risk-budget sized but floored at
    1 lot (futures are not fractional), blocked entirely if one lot risks >2%
    of equity. risk_cfg['fixed_lots'] bypasses guards for edge measurement
    (research only -- never use live without the guard math)."""
    if equity <= 0:
        return 0
    fixed = risk_cfg.get("fixed_lots")
    if fixed is not None:
        return max(1, int(fixed))
    risk_pct = float(risk_cfg.get("risk_per_trade_pct", 1.0))
    cap_per_10l = float(risk_cfg.get("max_lots_per_10l", 3))
    per_lot = stop_dist_pts * lot
    if per_lot <= 0 or per_lot > equity * 0.02:
        return 0
    lots = int(equity * risk_pct / 100.0 // per_lot)
    lots = max(lots, 1)
    cap = max(1, int(cap_per_10l * equity / 1_000_000))
    return min(lots, cap)


def walk_brackets(close: np.ndarray, hi: np.ndarray, lo: np.ndarray,
                  start_i: int, dir_: int, stop_px: float, target_px: float,
                  end_i: int) -> tuple[int, float, str]:
    """Walk minute bars after `start_i` (entry bar). Returns (exit_index, px, reason).
    Stop has priority when both levels are touchable within one bar."""
    for j in range(start_i + 1, end_i):
        if dir_ > 0:
            if lo[j] <= stop_px:
                return j, stop_px, "stop"
            if hi[j] >= target_px:
                return j, target_px, "target"
        else:
            if hi[j] >= stop_px:
                return j, stop_px, "stop"
            if lo[j] <= target_px:
                return j, target_px, "target"
    j = end_i - 1
    return j, float(close[j]), "time"


def make_trade(d, dir_: int, entry: float, exit_px: float, reason: str,
               lots: int, lot: int, fut_cost_fn) -> dict | None:
    if lots <= 0 or not np.isfinite(entry) or not np.isfinite(exit_px):
        return None
    entry, exit_px = round(float(entry), 2), round(float(exit_px), 2)
    pts = (exit_px - entry) * dir_
    cost = fut_cost_fn(entry, lots, lot)["total"]
    return {"date": str(d), "dir": dir_, "entry": entry,
            "exit": exit_px, "pts": round(float(pts), 2),
            "lots": lots, "cost": cost, "pnl": round(pts * lot * lots - cost, 2),
            "reason": reason}
