"""E. Conditional ORB: the sweep-1 lesson was that UNCONDITIONAL intraday breakout
dies to costs. This variant only arms on sessions following COMPRESSION (prior-day
true-range in the bottom X% of the quarter) -- coiled markets expand, quiet ones chop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.atlas import features as F


def filter_compression_days(min1: dict, nifty: pd.DataFrame, pctile_max: float,
                            lookback: int = 60) -> dict:
    rp = F.range_pctile(nifty, lookback)
    dates = sorted(min1)
    keep = []
    for d in dates:
        loc = nifty.index.searchsorted(pd.Timestamp(d), side="right") - 1
        if 0 < loc < len(nifty) and np.isfinite(rp.iloc[loc]) and rp.iloc[loc] <= pctile_max:
            keep.append(d)
    return {d: min1[d] for d in keep}


def generate_cond_orb_trades(min1: dict, nifty: pd.DataFrame, params: dict,
                             lot: int, fut_cost_fn, equity_fn, risk_cfg: dict) -> list[dict]:
    from src.strategies.intraday import generate_orb_trades
    pctile_max = float(params.get("prior_pctile_max", 0.35))
    orb_params = {k: v for k, v in params.items() if k != "prior_pctile_max"}
    filtered = filter_compression_days(min1, nifty, pctile_max)
    if not filtered:
        return []
    return generate_orb_trades(filtered, nifty, orb_params, lot,
                               fut_cost_fn, equity_fn, risk_cfg)
