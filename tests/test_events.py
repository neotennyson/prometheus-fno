import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.events import EventRisk


def make_cal(tmp_path, events):
    p = Path(tmp_path) / "events.yaml"
    p.write_text(yaml.safe_dump({"events": events}))
    return p


def make_erc(tmp_path, events, **cfg):
    base = dict(keywords=["rbi", "war"], poll_secs=0, alert_lookback_min=45,
                headline_action="pause", poll_window_start="00:00", poll_window_end="23:59")
    base.update(cfg)
    return EventRisk(make_cal(tmp_path, events), base, fetcher=lambda url: [])


def test_event_for_explicit_and_recurring(tmp_path):
    erc = make_erc(tmp_path, [
        {"name": "rbi_mpc", "date": "2026-09-30", "action": "no_entry"},
        {"name": "budget", "every_year": "02-01", "action": "no_entry"},
    ])
    assert erc.event_for(date(2026, 9, 30))["name"] == "rbi_mpc"
    assert erc.event_for(date(2027, 2, 1))["name"] == "budget"
    assert erc.event_for(date(2026, 5, 11)) is None


def test_headline_alert_keyword_and_lookback(tmp_path):
    erc = make_erc(tmp_path, [], feeds=["f1"])
    now = datetime(2026, 8, 25, 10, 0)
    erc._fetcher = lambda url: [{"title": "RBI cuts repo rate by 50bps", "summary": ""}]
    erc.refresh(now)
    a = erc.headline_alert(now)
    assert a and a["keyword"] == "rbi"
    old = datetime(2026, 8, 25, 9, 0)
    erc._headlines = [{**erc._headlines[0], "ts": old}]
    assert erc.headline_alert(now) is None


def test_assess_blocks_on_calendar_no_entry(tmp_path):
    erc = make_erc(tmp_path, [{"name": "budget", "date": "2026-02-01", "action": "no_entry"}])
    out = erc.assess(datetime(2026, 2, 1, 10, 0))
    assert out["block_entries"] and not out["force_flat"] and "budget" in out["reason"]


def test_assess_flat_action_forces_flat(tmp_path):
    erc = make_erc(tmp_path, [{"name": "war", "date": "2026-02-01", "action": "flat"}])
    out = erc.assess(datetime(2026, 2, 1, 10, 0))
    assert out["force_flat"] and out["block_entries"]


def test_assess_headline_pause(tmp_path):
    erc = make_erc(tmp_path, [], feeds=["f1"])
    now = datetime(2026, 8, 25, 10, 0)
    erc._fetcher = lambda url: [{"title": "War escalation fears grip markets", "summary": ""}]
    erc.refresh(now)
    out = erc.assess(now)
    assert out["block_entries"] and not out["force_flat"]
    erc.headline_action = "flat"
    out2 = erc.assess(now, refresh=False)
    assert out2["force_flat"]


def test_orchestrator_event_blocks_entries(tmp_path, monkeypatch):
    import src.orchestrator as orc
    from src.config import Settings
    from src.costs import CostModel
    from src.exec.paper_broker import PaperBroker

    cm = CostModel()

    def q(seg, sid):
        return 24150.0 if sid == "13" else 120.0

    b = PaperBroker(q, cm, db_path=str(Path(tmp_path) / "t.db"), starting_cash=500_000)
    o = orc.Orchestrator(Settings.load(), b, q)
    o.state_path = tmp_path / "state.json"
    o.state = o._load_state()
    o._day_start_equity = b.equity()
    o.events = make_erc(tmp_path, [{"name": "budget", "date": "2026-08-25", "action": "no_entry"}])
    o.is_expiry_day = lambda: True

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 25, 10, 6)

    with monkeypatch.context() as m:
        m.setattr(orc, "ist_now", FakeDT.now)
        out = o.run_once()
    joined = " ".join(map(str, out["actions"]))
    assert "EVENT_PAUSE" in joined
    assert "IF_ENTRY" not in joined
