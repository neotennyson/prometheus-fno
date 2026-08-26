import sys
from datetime import date
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.engine import Backtester
from src.backtest.metrics import perf_stats
from src.config import Settings, project_root
from src.data import ingest_yf


def month_add(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def sharpe_of(pnl, capital):
    if pnl is None or len(pnl) == 0:
        return -99.0
    st = perf_stats(pnl, capital)
    if st.get("trade_days", 0) < 8:
        return -99.0
    return st["sharpe"]


GRID = [
    {"wing_width": w, "target_keep_pct": k, "iv_to_vix_mult": v}
    for w, k, v in product([100, 175, 250], [0.35, 0.45], [1.00, 1.08])
]
DEFAULT = {"wing_width": 175, "target_keep_pct": 0.40, "iv_to_vix_mult": 1.08}


def main():
    cfg = Settings.load().raw
    nifty = ingest_yf.load_daily("nifty")
    vix = ingest_yf.load_daily("indiavix")["close"]

    start = date(2021, 8, 1)
    end = date(2026, 8, 22)
    train_months, test_months = 18, 6

    rows = []
    oos_pieces = []
    wstart = start
    wi = 0
    while month_add(wstart, train_months + test_months) <= month_add(end, 0):
        tr_s = wstart
        te_s = month_add(wstart, train_months)
        te_e = month_add(wstart, train_months + test_months)
        if te_e > end:
            break
        wi += 1

        scores = []
        for combo in GRID:
            bt = Backtester(cfg)
            pnl = bt.run_iron_fly(nifty, vix, {**cfg["strategies"]["iron_fly_expiry"], **combo},
                                  tr_s, te_s)
            scores.append((sharpe_of(pnl, cfg["capital"]), combo))

        scores.sort(key=lambda x: -x[0])
        best_sharpe, best_combo = scores[0]
        chosen = best_combo if best_sharpe > 0.5 else DEFAULT

        bt = Backtester(cfg)
        pnl_oos = bt.run_iron_fly(nifty, vix, {**cfg["strategies"]["iron_fly_expiry"], **chosen},
                                  te_s, te_e)
        st = perf_stats(pnl_oos, cfg["capital"]) if len(pnl_oos) else {"sharpe": 0, "cagr_pct": 0, "max_dd_pct": 0, "win_rate_pct": 0, "profit_factor": 0}
        oos_pieces.append(pnl_oos)
        rows.append({
            "window": f"{te_s.strftime('%Y-%m')}→{te_e.strftime('%Y-%m')}",
            "chosen": f"W{chosen['wing_width']}/K{int(chosen['target_keep_pct']*100)}/IV{chosen['iv_to_vix_mult']}",
            "train_sharpe": round(best_sharpe, 2),
            "oos_sharpe": st.get("sharpe", 0),
            "oos_cagr": st.get("cagr_pct", 0),
            "oos_dd": st.get("max_dd_pct", 0),
            "oos_winpct": st.get("win_rate_pct", 0),
        })
        print(f"[wf] window {wi}: {rows[-1]}", flush=True)
        wstart = month_add(wstart, test_months)

    df = pd.DataFrame(rows)
    combined = pd.concat(oos_pieces).groupby(level=0).sum().sort_index() if oos_pieces else pd.Series(dtype=float)
    agg = perf_stats(combined, cfg["capital"]) if len(combined) else {}
    out = project_root() / "results"
    df.to_csv(out / "walk_forward_windows.csv", index=False)
    print("\n================ WALK-FORWARD RESULTS (all windows are UNSEEN data) ================")
    print(df.to_string(index=False))
    print(f"\nAggregate stitched OOS curve: Sharpe {agg.get('sharpe')} | CAGR {agg.get('cagr_pct')}% | "
          f"MaxDD {agg.get('max_dd_pct')}% | Win% {agg.get('win_rate_pct')} | PF {agg.get('profit_factor')}")


if __name__ == "__main__":
    main()
