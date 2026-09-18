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


def test_paper_broker_short_add_avg_price(tmp_path):
    """Adding to a SHORT must average on absolute quantities.
    Regression: signed qty corrupted the average price."""
    from src.costs import CostModel
    from src.exec.paper_broker import PaperBroker
    px = {"px": 100.0}

    def q(seg, sid):
        return px["px"]

    pb = PaperBroker(q, CostModel(), db_path=str(tmp_path / "t.db"),
                     slippage_pct=0.0, min_slip=0.0)
    pb.market_order("NSE_FNO", "13", 1, "SELL")     # short 1 @ 100
    px["px"] = 110.0
    pb.market_order("NSE_FNO", "13", 1, "SELL")     # short 1 more @ 110
    pos = pb.pos_book["NSE_FNO:13"]
    assert pos.qty == -2
    assert abs(pos.avg_px - 105.0) < 1e-6           # (100+110)/2, not (100-110)/2


def test_paper_broker_flat_position(tmp_path):
    """flat_position used to reference the method instead of the book (TypeError)."""
    from src.costs import CostModel
    from src.exec.paper_broker import PaperBroker
    px = {"px": 100.0}

    def q(seg, sid):
        return px["px"]

    pb = PaperBroker(q, CostModel(), db_path=str(tmp_path / "t2.db"),
                     slippage_pct=0.0, min_slip=0.0)
    pb.market_order("NSE_FNO", "13", 1, "BUY")
    assert pb.flat_position("NSE_FNO", "13") is not None
    assert "NSE_FNO:13" not in pb.pos_book
    assert pb.flat_position("NSE_FNO", "13") is None    # nothing left to flat


def test_ist_clock_is_ist_even_on_utc_host():
    """Session clock must be pinned to Asia/Kolkata (UTC VPS deployment safety)."""
    from src.orchestrator import ist_now
    from zoneinfo import ZoneInfo
    now = ist_now()
    assert now.utcoffset().total_seconds() == 19800    # +5:30


def test_dhan_client_id_auto_extracted_from_jwt():
    """Paste ONE value: client id is parsed from the token's own JWT payload."""
    import base64
    import json
    from src.data.dhan_client import DhanClient
    claims = {"dhanClientId": "YOUR_DHAN_CLIENT_ID", "exp": 9999999999}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().strip("=")
    tok = f"eyJhbGciOiJots9.eyJ0ZXN0Ijp0cnVlfQ.sig"  # placeholder header/sig
    tok = f"aaa.{payload}.sig"
    assert DhanClient._client_id_from_jwt(tok) == "YOUR_DHAN_CLIENT_ID"
    assert DhanClient._client_id_from_jwt("garbage") is None
