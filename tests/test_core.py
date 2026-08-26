import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.costs import CostModel
from src.models.black_scholes import bs_price, implied_vol


def test_put_call_parity():
    S, K, T, sig, r = 25000.0, 25100.0, 5 / (252 * 375), 0.14, 0.065
    c = bs_price(S, K, T, sig, r=r, kind="CE")
    p = bs_price(S, K, T, sig, r=r, kind="PE")
    lhs = c - p
    rhs = S - K * pow(2.718281828459045, -r * T)
    assert abs(lhs - rhs) < max(1e-4, abs(rhs) * 1e-4)


def test_implied_vol_roundtrip():
    S, K, T, sig = 24500.0, 24600.0, 2 / (252 * 375), 0.18
    px = float(bs_price(S, K, T, sig, kind="PE"))
    iv = implied_vol(px, S, K, T, kind="PE")
    assert abs(iv - sig) < 1e-3


def test_option_cost_stack_new_stt():
    cm = CostModel()
    lots = 1
    lot = 65
    sell_premium = 100.0
    buy_premium = 60.0
    r = cm.option_round_trip(sell_premium, buy_premium, lots, lot)
    sell_val = sell_premium * lots * lot
    assert abs(r["stt"] - sell_val * 0.0015) < 0.01
    expected_exch = sell_val * 0.0003503 + buy_premium * lots * lot * 0.0003503
    assert abs(r["exchange"] - expected_exch) < 0.01
    assert r["slippage"] >= sell_premium * 0.002 * lots * lot


def test_futures_costs():
    cm = CostModel()
    r = cm.futures_round_trip(25000.0, 1, 65)
    val = 25000 * 65
    assert abs(r["total"] - (val * 0.0005 + val * 2 * 0.0000173 + val * 2 * 0.000001 + val * 0.00002 + 40 + 0.18 * (40 + val * 2 * 0.0000173 + val * 2 * 0.000001) + 195)) < 1.0
