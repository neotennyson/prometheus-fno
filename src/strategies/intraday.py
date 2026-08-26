"""Daily-frequency intraday strategies on the 1-min NIFTY spot panel.

Each generator mirrors house style (see gap_reversion): walk sessions chronologically,
emit trade dicts {date, dir, entry, exit, pts, lots, cost, pnl, reason}. All use
shared conservative execution from backtest.intraday_engine. These exist to put
controlled risk on EVERY session -- the existing engines only cover ~25% of days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.intraday_engine import (bar_minutes, make_trade, size_lots,
                                          tmin, walk_brackets)
from src.strategies.gap_reversion import atr_daily


def _daily_atr_map(daily: pd.DataFrame, period: int) -> dict:
    atr = atr_daily(daily, period)
    return {d.date() if hasattr(d, "date") else d: float(v)
            for d, v in atr.items()}


# ---------------------------------------------------------------- ORB breakout
def generate_orb_trades(min1: dict, daily: pd.DataFrame, params: dict,
                        lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    orb_bars = int(params.get("orb_minutes", 15))
    stop_mult = float(params.get("stop_atr_mult", 0.75))
    tp_r = float(params.get("tp_r", 2.0))
    cutoff = tmin(params.get("entry_cutoff", "12:00"))
    exit_min = tmin(params.get("exit_time", "15:15"))
    atr_p = int(params.get("atr_period", 14))
    atr_map = _daily_atr_map(daily, atr_p)

    trades = []
    for d, g in min1.items():
        mins = bar_minutes(g)
        c = g["close"].to_numpy()
        hi, lo = g["high"].to_numpy(), g["low"].to_numpy()
        mask_exit = mins < exit_min
        if len(c) < 250 or not mask_exit.any():
            continue
        atr = atr_map.get(d)
        if atr is None or not np.isfinite(atr) or atr <= 0:
            continue
        orh, orl = float(np.max(hi[:orb_bars])), float(np.min(lo[:orb_bars]))
        sig_i = None
        for i in range(orb_bars, len(c)):
            m = mins[i]
            if m >= min(cutoff, exit_min):
                break
            if c[i] > orh:
                sig_i, dir_ = i, 1
                break
            if c[i] < orl:
                sig_i, dir_ = i, -1
                break
        if sig_i is None:
            continue
        entry = float(c[sig_i])
        stop_d = stop_mult * atr
        stop_px = entry - dir_ * stop_d
        target_px = entry + dir_ * tp_r * stop_d
        equity = equity_fn(pd.Timestamp(d))
        lots = size_lots(equity, stop_d, lot, risk_cfg)
        if lots <= 0:
            continue
        end_i = int(np.flatnonzero(mask_exit)[-1]) + 1
        j, px, reason = walk_brackets(c, hi, lo, sig_i, dir_, stop_px, target_px, end_i)
        tr = make_trade(d, dir_, entry, px, reason, lots, lot, fut_cost_fn)
        if tr:
            trades.append(tr)
    return trades


# ---------------------------------------------------------------- VWAP/TWAP fade
def generate_vwap_fade_trades(min1: dict, daily: pd.DataFrame, params: dict,
                              lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    z_in = float(params.get("z_in", 2.0))
    z_out = float(params.get("z_out", 0.3))
    stop_z = float(params.get("stop_z", 3.5))
    win_start = tmin(params.get("window_start", "11:00"))
    win_end = tmin(params.get("window_end", "14:30"))
    exit_min = tmin(params.get("exit_time", "15:15"))
    std_win = int(params.get("std_window", 30))

    trades = []
    for d, g in min1.items():
        mins = bar_minutes(g)
        c = g["close"].to_numpy(dtype=float)
        if len(c) < 250:
            continue
        # TWAP proxy for VWAP (no spot volume at 1-min); deviation stats rolling
        run_ma = pd.Series(c).expanding(min_periods=30).mean().to_numpy()
        dev = pd.Series(c - run_ma)
        sd = dev.rolling(std_win, min_periods=std_win).std().to_numpy()
        z = np.where(sd > 0, dev.to_numpy() / np.where(sd > 0, sd, 1), np.nan)

        pos_dir, e_i, e_px, e_stop, e_lots = 0, -1, 0.0, np.nan, 0
        end_i = int(np.flatnonzero(mins < exit_min)[-1]) + 1 if (mins < exit_min).any() else 0
        for i in range(std_win + 30, len(c)):
            m = mins[i]
            if m >= exit_min:
                break
            zi = z[i]
            if pos_dir == 0:
                if not np.isfinite(zi) or not (win_start <= m <= win_end):
                    continue
                if zi <= -z_in or zi >= z_in:
                    pos_dir = 1 if zi <= -z_in else -1
                    e_i, e_px = i, float(c[i])
                    e_sd = max(float(sd[i]), 1e-9)
                    e_stop = e_px - stop_z * e_sd if pos_dir > 0 else e_px + stop_z * e_sd
                    e_lots = size_lots(equity_fn(pd.Timestamp(d)), stop_z * e_sd, lot, risk_cfg)
                continue
            # manage open position on closes
            hit_stop = c[i] <= e_stop if pos_dir > 0 else c[i] >= e_stop
            z_now = z[i]
            hit_target = np.isfinite(z_now) and ((pos_dir > 0 and z_now >= -z_out) or
                                                 (pos_dir < 0 and z_now <= z_out))
            if hit_stop or hit_target or i == end_i - 1:
                reason = "stop" if hit_stop else ("z_out" if hit_target else "time")
                tr = make_trade(d, pos_dir, e_px, float(c[i]), reason, e_lots,
                                lot, fut_cost_fn)
                if tr:
                    trades.append(tr)
                pos_dir = 0
    return trades


# ------------------------------------------------------- trend-day continuation
def generate_trend_day_trades(min1: dict, daily: pd.DataFrame, params: dict,
                              lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    drive_k = float(params.get("drive_atr", 0.6))
    trail_mult = float(params.get("trail_atr", 1.2))
    probe_min = tmin(params.get("probe_time", "10:30"))
    enter_after = tmin(params.get("entry_time", "10:45"))
    exit_min = tmin(params.get("exit_time", "15:15"))
    atr_p = int(params.get("atr_period", 14))
    atr_map = _daily_atr_map(daily, atr_p)

    trades = []
    for d, g in min1.items():
        mins = bar_minutes(g)
        c = g["close"].to_numpy(dtype=float)
        lo, hi = g["low"].to_numpy(), g["high"].to_numpy()
        if len(c) < 250:
            continue
        atr = atr_map.get(d)
        if atr is None or not np.isfinite(atr) or atr <= 0:
            continue
        pi = int(np.searchsorted(mins, probe_min))
        ei = int(np.searchsorted(mins, enter_after))
        if pi >= len(c) or ei >= len(c) or pi < 20:
            continue
        day_open = float(g["open"].iloc[0])
        move = float(c[pi]) - day_open
        morning_range = float(np.max(hi[:pi]) - np.min(lo[:pi]))
        if morning_range <= 0 or abs(move) < drive_k * atr:
            continue
        # continuation requires close in the extreme half of the morning range
        mid = np.min(lo[:pi]) + morning_range / 2.0
        if (move > 0 and c[pi] < mid) or (move < 0 and c[pi] > mid):
            continue
        dir_ = 1 if move > 0 else -1
        entry = float(c[ei])
        equity = equity_fn(pd.Timestamp(d))
        lots = size_lots(equity, trail_mult * atr, lot, risk_cfg)
        end_i = int(np.flatnonzero(mins < exit_min)[-1]) + 1 if (mins < exit_min).any() else len(c)
        best = entry
        exit_j, exit_px, reason = end_i - 1, float(c[end_i - 1]), "time"
        for j in range(ei + 1, end_i):
            best = max(best, c[j]) if dir_ > 0 else min(best, c[j])
            trail = (best - trail_mult * atr) if dir_ > 0 else (best + trail_mult * atr)
            breached = c[j] <= trail if dir_ > 0 else c[j] >= trail
            if breached:
                # conservative: assume the worse of close vs trail level
                px = min(float(c[j]), trail) if dir_ > 0 else max(float(c[j]), trail)
                exit_j, exit_px, reason = j, px, "trail"
                break
        tr = make_trade(d, dir_, entry, exit_px, reason, lots, lot, fut_cost_fn)
        if tr:
            trades.append(tr)
    return trades


REGISTRY = {
    "orb_intraday": generate_orb_trades,
    "vwap_fade": generate_vwap_fade_trades,
    "trend_day": generate_trend_day_trades,
}
