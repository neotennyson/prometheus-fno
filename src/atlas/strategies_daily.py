"""ATLAS daily-frequency specialists. House style: generators emit trade dicts
{date, dir, entry, exit, pts, lots, cost, pnl, reason}. Fills at the NEXT session's
open after a signal close (no lookahead), costs via CostModel.futures_round_trip,
conservative ambiguity handling (skip rather than guess).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.atlas import features as F
from src.backtest.intraday_engine import make_trade, size_lots


def _rt_cost(cm_fn, entry_px, exit_px, lots, lot):
    # charge round trip on average of entry/exit notionals (price drift over hold)
    return cm_fn((entry_px + exit_px) / 2.0, lots, lot)["total"]


# ------------------------------------------------------------- A. Donchian swing
def generate_donchian_trades(nifty: pd.DataFrame, params: dict,
                             lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    n_in = int(params.get("entry_n", 20))
    n_out = int(params.get("exit_n", 10))
    atr_n = int(params.get("atr_period", 14))
    vix_max = float(params.get("vix_max", 99))
    atr_s = F.atr(nifty, atr_n)

    dh = F.donchian_hi(nifty, n_in)
    dl = F.donchian_lo(nifty, n_in)
    xl = F.donchian_lo(nifty, n_out)
    xh = F.donchian_hi(nifty, n_out)

    trades = []
    pos_dir, entry_px, lots_held, entry_i = 0, 0.0, 0, -1
    pend = 0
    start = max(n_in, n_out, atr_n) + 2

    for i in range(start, len(nifty)):
        d = nifty.index[i]
        if pend != 0 and pos_dir == 0:                      # execute pending entry
            entry_px = float(nifty["open"].iloc[i])
            eq = equity_fn(d)
            stop_d = float(params.get("stop_atr", 3.0)) * float(atr_s.iloc[i])
            lots_held = size_lots(eq, stop_d, lot, risk_cfg)
            if lots_held > 0:
                pos_dir, entry_i = pend, i
            pend = 0
        elif pend != 0 and pos_dir != 0:                    # pending flip cancels old
            pend = 0

        vix_ok = True                                       # optional vix gate hook
        c = float(nifty["close"].iloc[i])

        if pos_dir != 0:
            exit_now = (pos_dir > 0 and c < float(xl.iloc[i])) or \
                       (pos_dir < 0 and c > float(xh.iloc[i]))
            if exit_now:
                exit_px = float(nifty["open"].iloc[min(i + 1, len(nifty) - 1)])
                cost = _rt_cost(fut_cost_fn, entry_px, exit_px, lots_held, lot)
                tr = make_trade(nifty.index[entry_i].date(), pos_dir, entry_px,
                                exit_px, "channel_exit", lots_held, lot,
                                lambda p, l, lt: {"total": cost})
                if tr:
                    trades.append(tr)
                pos_dir = 0
            continue

        if vix_ok and np.isfinite(dh.iloc[i]) and np.isfinite(dl.iloc[i]):
            if c > float(dh.iloc[i]):
                pend = 1
            elif c < float(dl.iloc[i]):
                pend = -1
    return trades


# ----------------------------------------------- B. NR7 / inside-bar expansion
def generate_nr7_trades(nifty: pd.DataFrame, params: dict,
                        lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    buf_atr = float(params.get("buf_atr", 0.10))
    pctile_max = float(params.get("range_pctile_max", 0.40))
    atr_s = F.atr(nifty, 14)
    coil = (F.nr7_flag(nifty, int(params.get("lookback", 7))) |
            F.inside_bar_flag(nifty)) & (F.range_pctile(nifty, 60) <= pctile_max)

    trades = []
    for i in np.flatnonzero(coil.to_numpy()):
        j = i + 1
        if j >= len(nifty):
            continue
        hi_prev, lo_prev = float(nifty["high"].iloc[i]), float(nifty["low"].iloc[i])
        a = float(atr_s.iloc[j])
        if not np.isfinite(a):
            continue
        buy_lvl, sell_lvl = hi_prev + buf_atr * a, lo_prev - buf_atr * a
        o, h, l, c = (float(nifty[k].iloc[j]) for k in ("open", "high", "low", "close"))
        up_first = l <= buy_lvl <= h
        dn_first = l <= sell_lvl <= h
        if up_first and dn_first:
            continue                                        # ambiguous: skip, never guess
        if not (up_first or dn_first):
            continue
        dir_ = 1 if up_first else -1
        lvl = buy_lvl if up_first else sell_lvl
        rev = sell_lvl if up_first else buy_lvl
        hit_rev = (l <= rev) if up_first else (h >= rev)
        exit_px = rev if hit_rev else c                     # reversal or EOD close
        eq = equity_fn(nifty.index[j])
        lots = size_lots(eq, abs(lvl - rev), lot, risk_cfg)
        tr = make_trade(nifty.index[j].date(), dir_, lvl, exit_px,
                        "reversal" if hit_rev else "eod", lots, lot, fut_cost_fn)
        if tr:
            trades.append(tr)
    return trades


# ------------------------------------------------------------ C. VIX spike fade
def generate_vix_spike_trades(nifty: pd.DataFrame, vix: pd.Series, params: dict,
                              lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    rank_q = float(params.get("rank_q", 0.95))
    idx_ret_min = float(params.get("idx_ret_min", -0.015))
    hold_days = int(params.get("hold_days", 4))
    stop_pct = float(params.get("stop_pct", 0.03))

    spike = F.vix_spike(vix).reindex(nifty.index)
    ret1 = nifty["close"].pct_change()
    vix_al = vix.reindex(nifty.index).ffill()

    trades = []
    i = 60
    while i < len(nifty) - 2:
        r = spike.iloc[i]
        if np.isfinite(r) and r >= rank_q and ret1.iloc[i] <= idx_ret_min:
            j = i + 1
            entry = float(nifty["open"].iloc[j])
            stop_px = entry * (1 - stop_pct)
            exit_px, reason, k = None, None, None
            for k in range(j, min(j + hold_days, len(nifty))):
                if float(nifty["close"].iloc[k]) < stop_px:
                    exit_px, reason = stop_px, "stop"
                    break
                if float(vix_al.iloc[k]) < float(vix_al.iloc[i]):
                    exit_px, reason = float(nifty["close"].iloc[k]), "vix_norm"
                    break
            if exit_px is None:
                k = min(j + hold_days, len(nifty)) - 1
                exit_px, reason = float(nifty["close"].iloc[k]), "time"
            eq = equity_fn(nifty.index[j])
            lots = size_lots(eq, stop_pct * entry, lot, risk_cfg)
            tr = make_trade(nifty.index[j].date(), 1, entry, exit_px, reason, lots, lot, fut_cost_fn)
            if tr:
                trades.append(tr)
            i = k + 1
        else:
            i += 1
    return trades


# ------------------------------------------------------- D. Global-night fade
def generate_global_fade_trades(nifty: pd.DataFrame, spx_overnight: pd.Series,
                                params: dict, lot: int, fut_cost_fn, equity_fn,
                                risk_cfg: dict) -> list[dict]:
    spx_thr = float(params.get("spx_ret_thr", -0.01))
    band = params.get("gap_band", "0.004,0.04")
    if isinstance(band, str):
        gap_lo, gap_hi = (float(x) for x in band.split(","))
    else:
        gap_lo, gap_hi = float(band[0]), float(band[1])
    stop_mult = float(params.get("stop_gap_mult", 0.75))
    prev_c = nifty["close"].shift(1)
    so = spx_overnight.reindex(nifty.index)

    trades = []
    for i in range(1, len(nifty)):
        s = so.iloc[i]
        if not np.isfinite(s) or s > spx_thr:
            continue
        o, h, l, c = (float(nifty[k].iloc[i]) for k in ("open", "high", "low", "close"))
        pc = float(prev_c.iloc[i])
        gap = o / pc - 1.0
        if not (gap_lo <= abs(gap) <= gap_hi) or gap > 0:
            continue                                        # fade DOWN gaps only
        # long from the gapped open: target = recovery to prior close,
        # stop BELOW entry at stop_mult x the gap distance.
        target = pc
        stop_px = o * (1 - stop_mult * abs(gap))
        hit_stop = l <= stop_px
        hit_tgt = h >= target
        if hit_stop and hit_tgt:
            hit_tgt = False                                 # ambiguous bar: stop first
        exit_px = stop_px if hit_stop else (target if hit_tgt else c)
        eq = equity_fn(nifty.index[i])
        lots = size_lots(eq, abs(o - stop_px), lot, risk_cfg)
        tr = make_trade(nifty.index[i].date(), 1, o, exit_px,
                        "stop" if hit_stop else ("target" if hit_tgt else "eod"),
                        lots, lot, fut_cost_fn)
        if tr:
            trades.append(tr)
    return trades
