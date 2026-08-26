from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.news_service import SEVERITY, classify, create_app


def test_classify_severity_levels():
    assert classify("RBI unexpectedly raises repo rate by 50bps")[0] == 3
    assert classify("SEBI bans promoter from markets")[0] == 2
    assert classify("Nifty ends flat in rangebound session")[0] == 0
    assert classify("Markets eye Fed chair speech", summary="fomc minutes released")[0] == 0


@pytest.fixture(scope="module")
def client():
    app = create_app({"poll_secs": 3600, "feeds": []})
    with TestClient(app) as c:
        yield c


def test_status_shape_and_webhook_flow(client):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    for key in ("impact_level", "block_entries", "force_flat", "recent_alerts"):
        assert key in body
    assert body["force_flat"] is False and body["block_entries"] is False

    w = client.post("/webhook", json={"title": "Government announces emergency imposed",
                                      "source": "manual"})
    assert w.status_code == 200 and w.json()["impact"] >= 3
    s = client.get("/status?minutes=60").json()
    assert s["force_flat"] is True and s["reason"]


def test_pulse_scoring():
    from src.news_service import pulse_score
    calm = pulse_score({"spx": 0.005, "nasdaq": 0.004, "crude": -0.01,
                        "usdinr": 0.001, "indiavix": -0.02})
    assert calm == (0, "calm")
    bad = pulse_score({"spx": -0.015, "nasdaq": -0.02, "crude": 0.03,
                       "usdinr": 0.006, "indiavix": 0.15})
    assert bad[0] == 100 and bad[1] == "risk_off"
    partial = pulse_score({"spx": -0.01, "nasdaq": 0.0, "crude": 0.0,
                           "usdinr": 0.0, "indiavix": 0.0})
    assert partial == (30, "elevated")
