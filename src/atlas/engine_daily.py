"""One generic, conservative daily backtester for every evolution-engine spec.

Conventions (match the rest of this repo):
- signal on close[i] -> fill at open[i+1]; one position at a time;
- SL/TP walks session H/L with STOP-FIRST on ambiguous bars; trail exits on close;
- costs: one futures round trip charged on the average of entry/exit notionals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def run_spec(aux: dict, long_mask: np.ndarray, short_mask: np.ndarray,
             exit_spec: tuple, lot: int, cost_fn) -> list[dict]:
    kind = exit_spec[0]
    o, h, l, c, atr = aux["o"], aux["h"], aux["l"], aux["c"], aux["atr"]
    dates = aux["dates"]
    n = len(c)
    trades: list[dict] = []
    i = 0

    while i < n - 1:
        if long_mask[i] == short_mask[i]:        # both true or both false -> flat
            i += 1
            continue
        dir_ = 1 if long_mask[i] else -1
        e = i + 1
        entry = o[e]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue
        a_e = atr[e]
        exit_px = None
        k = e

        if kind == "hold":
            k = min(e + int(exit_spec[1]) - 1, n - 1)
            exit_px = c[k]
            reason = "hold"
        elif kind == "so":
            # signal-off: ride while the entry condition persists, exit at the
            # close of the first session whose mask flips
            reason = "signal_off"
            for m in range(e, n):
                alive = long_mask[m] if dir_ > 0 else short_mask[m]
                if not bool(alive):
                    exit_px, k = c[m], m
                    break
            if exit_px is None:
                k, exit_px = n - 1, c[n - 1]
        elif kind == "sltp":
            s_mult, t_mult = float(exit_spec[1]), float(exit_spec[2])
            stop = entry - dir_ * s_mult * a_e
            target = entry + dir_ * t_mult * a_e
            for m in range(e + 1, n):
                hit_s = l[m] <= stop if dir_ > 0 else h[m] >= stop
                hit_t = h[m] >= target if dir_ > 0 else l[m] <= target
                if hit_s or hit_t:
                    if hit_s and hit_t:
                        hit_t = False            # ambiguous bar: stop first
                    exit_px = stop if hit_s else target
                    k, reason = m, ("stop" if hit_s else "target")
                    break
            if exit_px is None:
                k, exit_px, reason = n - 1, c[n - 1], "end"
        else:                                    # trail (chandelier on closes)
            mult = float(exit_spec[1])
            best = entry
            trail = entry - dir_ * mult * a_e
            reason = "trail"
            for m in range(e + 1, n):
                best = max(best, c[m]) if dir_ > 0 else min(best, c[m])
                trail = (best - mult * float(atr[m])) if dir_ > 0 else \
                        (best + mult * float(atr[m]))
                if (c[m] <= trail) if dir_ > 0 else (c[m] >= trail):
                    px = min(c[m], trail) if dir_ > 0 else max(c[m], trail)
                    exit_px, k = px, m
                    break
            if exit_px is None:
                k, exit_px, reason = n - 1, c[n - 1], "end"

        pts = (exit_px - entry) * dir_
        cost = cost_fn((entry + exit_px) / 2.0, 1, lot)["total"]
        trades.append({"date": str(pd.Timestamp(dates[e]).date()), "dir": dir_,
                       "entry": round(float(entry), 2), "exit": round(float(exit_px), 2),
                       "pts": round(float(pts), 2), "lots": 1, "cost": cost,
                       "pnl": round(pts * lot - cost, 2), "reason": reason})
        i = k + 1
    return trades
