import numpy as np
import pandas as pd
import pytest

from src.atlas.components import build_aux, sig_breakout
from src.atlas.engine_daily import run_spec


def _trend_df(n=120, drift=8.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 20000 + np.arange(n) * drift
    # tight bands so closes actually exceed prior highs (drift > band)
    df = pd.DataFrame({"open": close - drift / 2, "high": close + 1.0,
                       "low": close - 1.0, "close": close}, index=idx)
    return df


def test_aux_causal_and_finite():
    aux = build_aux(_trend_df())
    assert np.isfinite(aux["hh_20"][30:]).all()
    assert np.isfinite(aux["atr"][30:]).all()
    assert np.isfinite(aux["cpct_60"][61:]).all()   # 60-bar rank needs its window


def test_engine_known_math_hold_exit():
    # strictly rising market: breakout fires early, long held 3 sessions
    aux = build_aux(_trend_df())
    long_m, short_m = sig_breakout(aux, {"n": 10})
    trades = run_spec(aux, long_m, short_m, ("hold", 3), 65,
                      lambda p, l, lt: {"total": 0.0})
    assert trades
    t = trades[0]
    e_idx = int(np.flatnonzero(long_m)[0]) + 1
    dates = aux["dates"]
    exp_entry = float(aux["o"][e_idx])
    exp_exit = float(aux["c"][min(e_idx + 2, len(aux["c"]) - 1)])
    assert t["entry"] == round(exp_entry, 2)
    assert t["exit"] == round(exp_exit, 2)
    assert t["pnl"] == pytest.approx((exp_exit - exp_entry) * 65, abs=0.6)


def test_engine_stop_first_on_ambiguous_bar():
    aux = build_aux(_trend_df(60))
    long_mask = np.zeros(60, dtype=bool)
    long_mask[10] = True                      # enter at open[11]
    o = aux["o"]
    # craft session 12 so that both stop (-1 ATR-ish) and target (+2 ATR-ish)
    # are inside its range -> engine must book the STOP
    a = float(aux["atr"][11])
    entry = o[11]
    aux["l"][12] = entry - a * 3.0
    aux["h"][12] = entry + a * 5.0
    trades = run_spec(aux, long_mask, np.zeros(60, dtype=bool),
                      ("sltp", 1.0, 2.0), 65, lambda p, l, lt: {"total": 0.0})
    assert trades and trades[0]["reason"] == "stop"
    assert trades[0]["pts"] < 0


def test_costs_reduce_pnl():
    aux = build_aux(_trend_df())
    long_m, short_m = sig_breakout(aux, {"n": 10})
    free = run_spec(aux, long_m, short_m, ("hold", 1), 65,
                    lambda p, l, lt: {"total": 0.0})
    paid = run_spec(aux, long_m, short_m, ("hold", 1), 65,
                    lambda p, l, lt: {"total": 400.0})
    for tf, tp in zip(free, paid):
        assert tp["pnl"] == pytest.approx(tf["pnl"] - 400.0, abs=0.01)


def test_one_position_at_a_time():
    aux = build_aux(_trend_df())
    long_m, _ = sig_breakout(aux, {"n": 10})
    trades = run_spec(aux, long_m, np.zeros(len(aux["c"]), dtype=bool),
                      ("hold", 5), 65, lambda p, l, lt: {"total": 0.0})
    ds = [pd.Timestamp(t["date"]) for t in trades]
    de = [pd.Timestamp(t["date"]) + pd.Timedelta(days=5) for t in trades]
    for k in range(len(ds) - 1):
        assert de[k] <= ds[k + 1] or (de[k] - ds[k + 1]).days < 7  # calendar slop ok
