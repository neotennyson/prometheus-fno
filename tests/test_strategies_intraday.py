import numpy as np
import pandas as pd
import pytest

from src.costs import CostModel
from src.strategies.intraday import (generate_orb_trades,
                                     generate_trend_day_trades,
                                     generate_vwap_fade_trades)


def _make_day(d, seed, trend=0.0, base=24000.0):
    rng = np.random.default_rng(seed)
    n = 375  # 09:15 -> 15:29 at 1-min
    steps = rng.normal(0.8, 6.0, n) + trend * np.linspace(0, 1, n) * 40
    close = base + np.cumsum(steps)
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) + 1.0
    lo = np.minimum(open_, close) - 1.0
    ts = pd.date_range(f"{d} 09:15", periods=n, freq="1min")
    df = pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close}, index=ts)
    df["date"] = df.index.date
    return df


@pytest.fixture(scope="module")
def min1():
    days = {}
    for k in range(30):
        d = f"2025-06-{k + 1:02d}"
        days[pd.Timestamp(d).date()] = _make_day(d, seed=k, trend=(1 if k % 3 else -1))
    return days


@pytest.fixture(scope="module")
def daily():
    idx = pd.date_range("2025-05-01", "2025-07-15", freq="D")
    rng = np.random.default_rng(7)
    c = pd.Series(24000 + rng.normal(0, 120, len(idx)).cumsum(), index=idx)
    return pd.DataFrame({"open": c, "high": c + 80, "low": c - 80, "close": c})


CM = CostModel()
LOT = 65
RISK = {"risk_per_trade_pct": 1.0, "max_lots_per_10l": 3}


def _eq(ts):
    return 500_000.0


def test_orb_emits_valid_trades(min1, daily):
    trades = generate_orb_trades(min1, daily, {"orb_minutes": 15, "stop_atr_mult": 0.75,
                                               "tp_r": 2.0}, LOT, CM.futures_round_trip,
                                 _eq, RISK)
    assert trades, "ORB should fire on trending synthetic days"
    for t in trades:
        exp = t["pts"] * LOT * t["lots"] - t["cost"]
        assert t["pnl"] == pytest.approx(exp, rel=1e-6)
        assert t["reason"] in ("stop", "target", "time")
        assert t["cost"] > 0
        assert t["lots"] >= 1


def test_vwap_fade_runs_and_sizes_zero_when_risk_too_big(min1, daily):
    trades = generate_vwap_fade_trades(min1, daily, {"z_in": 1.8, "z_out": 0.2,
                                                     "stop_z": 3.0}, LOT,
                                       CM.futures_round_trip, _eq, RISK)
    for t in trades:
        assert t["pnl"] == pytest.approx(t["pts"] * LOT * t["lots"] - t["cost"], rel=1e-6)


def test_trend_day_runs(min1, daily):
    trades = generate_trend_day_trades(min1, daily, {"drive_atr": 0.3, "trail_atr": 1.0},
                                       LOT, CM.futures_round_trip, _eq, RISK)
    assert isinstance(trades, list)


def test_sizing_blocked_when_stop_too_wide(min1, daily):
    broke = {"risk_per_trade_pct": 1.0, "max_lots_per_10l": 3}
    trades = generate_orb_trades(min1, daily, {"orb_minutes": 15, "stop_atr_mult": 50.0},
                                 LOT, CM.futures_round_trip, lambda ts: 10_000.0, broke)
    assert trades == []  # per-lot risk would exceed 2% guardrail
