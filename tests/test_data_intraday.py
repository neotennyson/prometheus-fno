import pandas as pd
import pytest

from src.data.intraday import load_spot_1min


@pytest.fixture(scope="module")
def panel():
    return load_spot_1min()


def test_panel_loads_real_data(panel):
    assert len(panel) > 300_000          # ~1200 sessions x 375 bars
    days = panel["date"].unique()
    assert len(days) > 1000
    assert (panel["close"] > 100).all()
    idx = panel.index
    assert idx.is_monotonic_increasing
    assert not idx.duplicated().any()


def test_no_lookahead_in_open_proxy(panel):
    day = panel[panel["date"] == panel["date"].iloc[0]]
    assert day["open"].iloc[0] == day["close"].iloc[0]   # first bar anchors to itself
    # open[i] must equal close[i-1] within the session (proxy construction)
    o = day["open"].to_numpy()
    c = day["close"].to_numpy()
    import numpy as np
    assert np.allclose(o[1:], c[:-1])


def test_session_window_respected(panel):
    times = panel.index.time
    t = pd.Series(times)
    assert (t >= pd.Timestamp("09:15").time()).all()
    assert (t <= pd.Timestamp("15:30").time()).all()
