"""Bulk research harness: grid-sweep every registered intraday strategy over the
real 1-min panel, score with conviction(), promote winners to paper-trading config.

Walk-forward convention matches the house reports: IS = before 2024-01-01,
OOS = from 2024-01-01. Equity compounds through the full run; stats are then cut.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.intraday_engine import make_trade  # noqa: F401 (re-export convenience)
from src.config import Settings
from src.costs import CostModel
from src.data.intraday import load_spot_1min, repo_root, split_by_day
from src.research.conviction import PROMOTE_THRESHOLD, conviction_score, quarter_consistency
from src.strategies.intraday import REGISTRY

OOS_START = pd.Timestamp("2024-01-01")

GRIDS = {
    "orb_intraday": {
        "orb_minutes": [15, 30],
        "stop_atr_mult": [0.5, 0.75, 1.0],
        "tp_r": [1.5, 2.0, 3.0],
    },
    "vwap_fade": {
        "z_in": [1.8, 2.2],
        "z_out": [0.2, 0.5],
        "stop_z": [3.0, 3.5],
    },
    "trend_day": {
        "drive_atr": [0.5, 0.7],
        "trail_atr": [1.0, 1.5],
    },
}


def load_daily() -> pd.DataFrame:
    p = repo_root() / "data" / "raw" / "nifty_daily.parquet"
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index)
    return df


def run_combo(fn, min1: dict, daily: pd.DataFrame, params: dict,
              cm: CostModel, lot: int, capital: float, risk_cfg: dict) -> list[dict]:
    fut_cost_fn = cm.futures_round_trip
    equity = {"v": capital}
    start = daily.index[0]

    def equity_fn(ts):
        return max(equity["v"], capital * 0.05)

    trades = fn(min1, daily, params, lot, fut_cost_fn, equity_fn, risk_cfg)
    # compound sequentially so sizing reflects realized P&L
    trades.sort(key=lambda t: t["date"])
    for tr in trades:
        equity["v"] += tr["pnl"]
        tr["equity"] = equity["v"]
    return trades


def trades_to_daily(trades: list[dict]) -> pd.Series:
    if not trades:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(t["date"]): t["pnl"] for t in trades})
    return s.groupby(level=0).sum().sort_index()


def evaluate(name: str, fn, min1: dict, daily: pd.DataFrame, params: dict,
             cm: CostModel, lot: int, capital: float, risk_cfg: dict,
             sibling_pnls: list[float]) -> dict:
    from src.backtest.metrics import perf_stats
    trades = run_combo(fn, min1, daily, params, cm, lot, capital, risk_cfg)
    pnl = trades_to_daily(trades)
    full = perf_stats(pnl, capital)
    if len(pnl):
        pnl_is = pnl[pnl.index < OOS_START]
        pnl_oos = pnl[pnl.index >= OOS_START]
    else:
        pnl_is = pnl_oos = pnl
    st_is = perf_stats(pnl_is, capital)
    st_oos = perf_stats(pnl_oos, capital)
    plateau = float(np.mean([p > 0 for p in sibling_pnls])) if sibling_pnls else None
    qh = quarter_consistency(pnl_oos) if len(pnl_oos) else 0.0
    score = conviction_score(st_oos if st_oos.get("days") else full, plateau, qh)
    return {"strategy": name, "params": json.dumps(params), "score": score,
            "full_sharpe": full.get("sharpe"), "is_sharpe": st_is.get("sharpe"),
            "oos_sharpe": st_oos.get("sharpe"), "oos_cagr_pct": st_oos.get("cagr_pct"),
            "oos_max_dd_pct": st_oos.get("max_dd_pct"),
            "oos_pf": st_oos.get("profit_factor"),
            "oos_trade_days": st_oos.get("trade_days"), "oos_days": st_oos.get("days"),
            "quarters_hit": round(qh, 3),
            "full_total_pnl": full.get("total_pnl")}


def run_sweep(out_dir: str | Path | None = None, capital: float | None = None) -> pd.DataFrame:
    settings = Settings.load()
    cfg = settings.raw
    capital = capital or float(cfg.get("capital", 500_000))
    cm = CostModel.from_cfg(cfg["costs"])
    lot = int(cfg["universe"]["lot_size"])
    risk_cfg = cfg["risk"]

    print("[sweep] loading 1-min spot panel ...")
    panel = load_spot_1min()
    min1 = split_by_day(panel)
    daily = load_daily()
    print(f"[sweep] {len(min1)} sessions, {len(panel):,} minute bars")

    rows = []
    for name, fn in REGISTRY.items():
        grid = GRIDS[name]
        keys = list(grid)
        combos = [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
        # first pass: raw pnl per combo for plateau scoring
        raw = []
        for params in combos:
            trs = run_combo(fn, min1, daily, params, cm, lot, capital, risk_cfg)
            raw.append(sum(t["pnl"] for t in trs))
        for params, sib in zip(combos, raw):
            siblings = [x for j, x in enumerate(raw)]  # plateau vs whole family
            rows.append(evaluate(name, fn, min1, daily, params, cm, lot,
                                 capital, risk_cfg, siblings))
        print(f"[sweep] {name}: {len(combos)} combos done")

    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    out = Path(out_dir) if out_dir else repo_root() / "results" / "research"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "sweep_report.csv", index=False)
    promoted = df[df["score"] >= PROMOTE_THRESHOLD]
    summary = {
        "generated": pd.Timestamp.now().isoformat(),
        "sessions": len(min1),
        "combos": len(df),
        "promoted_count": int(len(promoted)),
        "promoted": promoted.to_dict("records"),
    }
    (out / "sweep_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"[sweep] report -> {out}/sweep_report.csv  "
          f"({len(promoted)}/{len(df)} combos above conviction {PROMOTE_THRESHOLD})")
    return df


if __name__ == "__main__":
    run_sweep()
