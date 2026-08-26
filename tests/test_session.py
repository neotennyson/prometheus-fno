import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.config import Settings
from src.costs import CostModel
from src.exec.paper_broker import PaperBroker
from src.data.calendar import weekly_expiries_between


def make_broker(tmp_path):
    from pathlib import Path as _P
    cm = CostModel()
    def q(seg, sid):
        return 24150.0 if sid == "13" else 120.0
    return PaperBroker(q, cm, db_path=str(_P(tmp_path) / "t.db"), starting_cash=500_000)


def test_expiry_tuesdays_sanity():
    from datetime import date as D
    exps = weekly_expiries_between(D(2026, 8, 1), D(2026, 8, 31))
    assert all(e.weekday() == 0 or e.weekday() == 1 for e in exps)
    assert len(exps) == 4


def test_paper_fill_and_flat(tmp_path):
    b = make_broker(tmp_path)
    r1 = b.market_order("NSE_FNO", "99999", 65, "SELL", tag="test")
    assert r1["px"] < 120.0, "sell should fill below raw with slippage"
    r2 = b.market_order("NSE_FNO", "99999", 65, "BUY", tag="test")
    assert len(b.positions()) == 0
    assert b.realized_today < 0, "round trip should lose costs+slippage"


def test_kill_switch_blocks_after_loss(tmp_path):
    b = make_broker(tmp_path)
    o = __import__("src.orchestrator", fromlist=["Orchestrator"]).Orchestrator(
        Settings.load(), b, lambda s, x: 24000.0)
    o._day_start_equity = 500_000
    b.cash = 480_000
    out = o.run_once()
    assert any("KILL_SWITCH" in str(a) for a in out["actions"])


def test_full_session_phases(tmp_path, monkeypatch):
    import src.orchestrator as orc
    b = make_broker(tmp_path)
    o = orc.Orchestrator(Settings.load(), b, lambda s, x: 24150.0)
    o.state_path = tmp_path / "state.json"
    o.state = o._load_state()
    o._day_start_equity = b.equity()
    o.events = None

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 25, o.clock_h, o.clock_m)
    o.clock_h, o.clock_m = 9, 20
    with patch.object(orc, "ist_now", FakeDT.now):
        a1 = o.run_once()["actions"]
    o.clock_h, o.clock_m = 10, 6
    with patch.object(orc, "ist_now", FakeDT.now):
        a2 = o.run_once()["actions"]
    o.clock_h, o.clock_m = 15, 26
    with patch.object(orc, "ist_now", FakeDT.now):
        a3 = o.run_once()["actions"]

    joined = " ".join(map(str, a1 + a2 + a3))
    assert "GAP_NONE" in joined or "GAP_SKIP" in joined or "GAP_FADE" in joined
    assert "IF_SKIP" in joined or "IF_ENTRY" in joined
    assert "ON_SKIP" in joined or "ON_ENTRY" in joined or "EOD_FLAT" in joined
