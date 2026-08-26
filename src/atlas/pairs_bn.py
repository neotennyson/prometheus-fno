"""Classic quant stat-arb: NIFTY vs BankNifty log-spread mean reversion.

The pair shares India beta; the spread is flows/sentiment between large- and
broad-cap banks vs the full index. When the 60d z-score of the log-ratio hits
+/-2, trade convergence: rich leg shorted, cheap leg longed. Two futures legs
-> double cost stack charged honestly. Rare trades by design.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def generate_pairs_trades(nifty: pd.DataFrame, banknifty: pd.DataFrame,
                          params: dict, lot_nf: int, lot_bn: int,
                          fut_cost_fn, equity_fn) -> list[dict]:
    window = int(params.get("window", 60))
    z_in = float(params.get("z_in", 2.0))
    z_out = float(params.get("z_out", 0.5))
    max_hold = int(params.get("max_hold", 20))

    nf_close = nifty["close"].copy()
    nf_close.index = pd.to_datetime(nf_close.index).normalize()
    bn_close = banknifty["close"].copy()
    bn_close.index = pd.to_datetime(bn_close.index).normalize()
    common = bn_close.index.intersection(nf_close.index)
    bn = bn_close.reindex(common).dropna()
    nf = nf_close.reindex(common).dropna()
    spread = np.log(bn) - np.log(nf)
    mu = spread.rolling(window).mean()
    sd = spread.rolling(window).std()
    z = (spread - mu) / sd

    trades = []
    pos = 0          # +1: spread rich -> short BN long NF; -1 mirror
    e_i = hold = 0
    entry_z = 0.0
    for i in range(window, len(z)):
        zi = z.iloc[i]
        if pos == 0:
            if not np.isfinite(zi):
                continue
            if zi >= z_in:
                pos, e_i, hold = 1, i, 0
            elif zi <= -z_in:
                pos, e_i, hold = -1, i, 0
            continue
        hold += 1
        closed = (pos > 0 and zi <= z_out) or (pos < 0 and zi >= -z_out) or \
                 hold >= max_hold or not np.isfinite(zi)
        if closed:
            # pnl proxied on the NF leg notional with BN excess applied via pts;
            # honest accounting needs both notionals -- approximate with ratio
            # of legs' point moves scaled to their lots.
            pts_nf = float(nf.iloc[i] - nf.iloc[e_i])
            pts_bn = float(bn.iloc[i] - bn.iloc[e_i])
            dir_ = -pos                      # spread converging direction
            pnl_pts = dir_ * (pts_nf * lot_nf - pts_bn * lot_bn * (nf.iloc[e_i] / bn.iloc[e_i]))
            cost = 2 * fut_cost_fn(float(nf.iloc[e_i]), 1, lot_nf)["total"]
            eq = equity_fn(nifty.index[i])
            lots = max(1, min(int(eq / (float(nf.iloc[e_i]) * lot_nf * 3)), 2)) if eq > 0 else 0
            if lots:
                trades.append({
                    "date": str(nifty.index[e_i].date()), "dir": dir_,
                    "entry": round(float(spread.iloc[e_i]), 4),
                    "exit": round(float(spread.iloc[i]), 4),
                    "pts": round(float(pnl_pts / lot_nf), 2),
                    "lots": lots, "cost": cost * lots,
                    "pnl": round(float(pnl_pts) * lots - cost * lots, 2),
                    "reason": "converged" if hold < max_hold else "time",
                })
            pos = 0
    return trades
