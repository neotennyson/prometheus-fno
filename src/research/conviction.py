"""Conviction score: one number deciding whether a tested configuration earns a
paper-trading slot. Composite of OOS performance, param-plateau breadth, quarterly
consistency, and -- because the mandate is daily capital deployment -- day coverage.

Score in [0, 1]. >= PROMOTE_THRESHOLD advances to paper. Components are intentionally
simple and auditable; no black-box blending.
"""
from __future__ import annotations

import math

PROMOTE_THRESHOLD = 0.60

WEIGHTS = {
    "sharpe": 0.28,      # tanh(sharpe/2): 2.0+ saturates
    "drawdown": 0.16,    # 1 - |maxDD|/40%
    "profit_factor": 0.14,
    "consistency": 0.12, # share of profitable quarters (OOS)
    "plateau": 0.12,     # share of neighbouring param combos with positive OOS pnl
    "coverage": 0.18,    # share of OOS sessions actually traded (mandate: daily)
}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def conviction_score(oos_stats: dict,
                     plateau: float | None = None,
                     quarters_hit: float | None = None) -> float:
    """oos_stats: perf_stats()-style dict (needs sharpe, max_dd_pct, profit_factor,
    trade_days, days). plateau: fraction of sibling combos with positive OOS pnl
    (None -> neutral 0.5). quarters_hit: fraction of profitable quarters (None -> 0.5)."""
    if not oos_stats or oos_stats.get("days", 0) == 0 or oos_stats.get("trade_days", 0) == 0:
        return 0.0
    sharpe_c = _clip01(math.tanh(float(oos_stats.get("sharpe", 0.0)) / 2.0))
    dd_c = _clip01(1.0 - abs(float(oos_stats.get("max_dd_pct", -100.0))) / 40.0)
    pf = float(oos_stats.get("profit_factor", 0.0))
    pf_c = _clip01((pf - 1.0) / 1.5) if math.isfinite(pf) else 1.0
    cons_c = _clip01(quarters_hit) if quarters_hit is not None else 0.5
    plat_c = _clip01(plateau) if plateau is not None else 0.5
    cov_raw = float(oos_stats["trade_days"]) / float(oos_stats["days"])
    cov_c = _clip01(cov_raw / 0.80)          # >=80% of sessions = full marks
    parts = {"sharpe": sharpe_c, "drawdown": dd_c, "profit_factor": pf_c,
             "consistency": cons_c, "plateau": plat_c, "coverage": cov_c}
    return round(sum(w * parts[k] for k, w in WEIGHTS.items()), 4)


def quarter_consistency(daily_pnl) -> float:
    """Fraction of calendar quarters with positive summed pnl."""
    if daily_pnl is None or len(daily_pnl) == 0:
        return 0.0
    q = daily_pnl.groupby([daily_pnl.index.year, daily_pnl.index.quarter]).sum()
    return float((q > 0).mean()) if len(q) else 0.0
