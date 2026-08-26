import numpy as np
import pandas as pd
import pytest

from src.research.conviction import PROMOTE_THRESHOLD, conviction_score, quarter_consistency


def _stats(sharpe=1.5, dd=-10.0, pf=1.8, trade_days=180, days=200):
    return {"sharpe": sharpe, "max_dd_pct": dd, "profit_factor": pf,
            "trade_days": trade_days, "days": days}


def test_score_in_bounds_and_zero_when_no_trades():
    assert 0.0 <= conviction_score(_stats()) <= 1.0
    assert conviction_score({}) == 0.0
    assert conviction_score(_stats(trade_days=0)) == 0.0


def test_score_monotonic_in_sharpe():
    lo = conviction_score(_stats(sharpe=0.3))
    hi = conviction_score(_stats(sharpe=2.5))
    assert hi > lo


def test_coverage_rewarded_per_daily_mandate():
    daily = conviction_score(_stats(trade_days=190))     # ~95% sessions
    sparse = conviction_score(_stats(trade_days=50))     # expiry-only cadence
    assert daily > sparse + 0.05


def test_plateau_neutral_by_default():
    a = conviction_score(_stats(), plateau=None)
    b = conviction_score(_stats(), plateau=0.5)
    assert abs(a - b) < 1e-9


def test_promotion_threshold_sanity():
    strong = conviction_score(_stats(sharpe=2.2, dd=-6.0, pf=2.4),
                              plateau=0.9, quarters_hit=0.8)
    weak = conviction_score(_stats(sharpe=0.4, dd=-25.0, pf=1.05), quarters_hit=0.3)
    assert strong >= PROMOTE_THRESHOLD > weak


def test_quarter_consistency():
    idx = pd.date_range("2024-01-01", periods=360, freq="D")
    pnl = pd.Series(np.where(idx.month <= 6, 10.0, -10.0), index=idx)  # 2 good qtrs, 2 bad
    assert quarter_consistency(pnl) == pytest.approx(0.5)
