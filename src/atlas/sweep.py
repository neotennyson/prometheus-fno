"""ATLAS bulk sweep: five candidate specialists over real data, conviction-scored,
IS/OOS split at the house line (2024-01-01), then greedy portfolio assembly with a
correlation cap. Everything lands in results/atlas/. Losers are reported as losers.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.atlas import features as F
from src.atlas.cond_orb import generate_cond_orb_trades
from src.atlas.data_global import load_daily, load_global, repo_root, spx_overnight_map
from src.backtest.intraday_engine import make_trade  # noqa: F401
from src.config import Settings
from src.costs import CostModel
from src.data.intraday import load_spot_1min, split_by_day
from src.research.conviction import PROMOTE_THRESHOLD, conviction_score, quarter_consistency
from src.strategies.gap_reversion import atr_daily  # noqa: F401
from src.strategies.intraday import REGISTRY as _MIN1_REGISTRY  # noqa: F401

OOS_START = pd.Timestamp("2024-01-01")

GRIDS = {
    "donchian": {
        "entry_n": [20, 55],
        "exit_n": [10, 20],
        "stop_atr": [3.0],
    },
    "nr7_bracket": {
        "buf_atr": [0.05, 0.15],
        "range_pctile_max": [0.35, 0.50],
        "lookback": [7],
    },
    "vix_spike_fade": {
        "rank_q": [0.90, 0.95],
        "idx_ret_min": [-0.010, -0.020],
        "hold_days": [3, 5],
    },
    "global_night_fade": {
        "spx_ret_thr": [-0.008, -0.015],
        "stop_gap_mult": [0.5, 1.0],
    },
    "cond_orb": {
        "prior_pctile_max": [0.30, 0.45],
        "orb_minutes": [15, 30],
        "stop_atr_mult": [0.75],
        "tp_r": [2.0],
    },
}


def build_context():
    nifty = load_daily("nifty")
    vix = load_daily("indiavix")["close"]
    spx = load_global("spx", refresh=True)
    spx_on = spx_overnight_map(nifty.index, spx)
    min1 = split_by_day(load_spot_1min())
    return {"nifty": nifty, "vix": vix, "spx_on": spx_on, "min1": min1}


def make_runner(name: str):
    if name == "donchian":
        return lambda c, p, lot, fn, eq, risk: __import__(
            "src.atlas.strategies_daily", fromlist=["x"]).generate_donchian_trades(
                c["nifty"], p, lot, fn, eq, risk)
    if name == "nr7_bracket":
        return lambda c, p, lot, fn, eq, risk: __import__(
            "src.atlas.strategies_daily", fromlist=["x"]).generate_nr7_trades(
                c["nifty"], p, lot, fn, eq, risk)
    if name == "vix_spike_fade":
        return lambda c, p, lot, fn, eq, risk: __import__(
            "src.atlas.strategies_daily", fromlist=["x"]).generate_vix_spike_trades(
                c["nifty"], c["vix"], p, lot, fn, eq, risk)
    if name == "global_night_fade":
        return lambda c, p, lot, fn, eq, risk: __import__(
            "src.atlas.strategies_daily", fromlist=["x"]).generate_global_fade_trades(
                c["nifty"], c["spx_on"], p, lot, fn, eq, risk)
    if name == "cond_orb":
        return lambda c, p, lot, fn, eq, risk: generate_cond_orb_trades(
            c["min1"], c["nifty"], p, lot, fn, eq, risk)
    raise KeyError(name)


def trades_to_daily(trades: list[dict], index: pd.DatetimeIndex) -> pd.Series:
    if not trades:
        return pd.Series(0.0, index=index)
    s = pd.Series({pd.Timestamp(t["date"]): t["pnl"] for t in trades})
    s = s.groupby(level=0).sum().sort_index()
    # daily parquet indexes carry a session-time component (e.g. 09:15);
    # normalize both sides or the join silently zeroes everything.
    return s.reindex(index.normalize()).fillna(0.0)


def run_sweep(out_dir: str | Path | None = None, capital: float | None = None) -> pd.DataFrame:
    from src.backtest.metrics import perf_stats
    cfg = Settings.load().raw
    capital = capital or float(cfg.get("capital", 500_000))
    cm = CostModel.from_cfg(cfg["costs"])
    lot = int(cfg["universe"]["lot_size"])
    risk_cfg = cfg["risk"]

    print("[atlas] building context (fetching global proxies if stale) ...")
    ctx = build_context()
    nifty_index = ctx["nifty"].index
    print(f"[atlas] {len(nifty_index)} sessions "
          f"({nifty_index[0].date()} -> {nifty_index[-1].date()}), "
          f"{len(ctx['min1'])} 1-min sessions")

    rows, oos_pnls = [], {}
    for name in GRIDS:
        runner = make_runner(name)
        grid = GRIDS[name]
        keys = list(grid)
        combos = [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
        raw = []
        for params in combos:
            trs = runner(ctx, params, lot, cm.futures_round_trip,
                         lambda ts: capital, {**risk_cfg, 'fixed_lots': 1})
            raw.append(sum(t["pnl"] for t in trs))
        for params, sib in zip(combos, raw):
            trs = runner(ctx, params, lot, cm.futures_round_trip,
                         lambda ts: capital, {**risk_cfg, 'fixed_lots': 1})
            pnl = trades_to_daily(trs, nifty_index)
            full = perf_stats(pnl[pnl != 0].groupby(level=0).sum(), capital) \
                if (pnl != 0).any() else {"days": 0}
            oos = pnl[pnl.index >= OOS_START]
            st_oos = perf_stats(oos[oos != 0].groupby(level=0).sum(), capital) \
                if (oos != 0).any() else {"days": 0}
            is_p = pnl[pnl.index < OOS_START]
            st_is = perf_stats(is_p[is_p != 0].groupby(level=0).sum(), capital) \
                if (is_p != 0).any() else {"days": 0}
            plateau = float(np.mean([p > 0 for p in raw])) if raw else 0.0
            qh = quarter_consistency(oos[oos != 0]) if (oos != 0).any() else 0.0
            score = conviction_score(st_oos if st_oos.get("days") else full,
                                     plateau, qh)
            oos_pnls[f"{name}|{json.dumps(params)}"] = oos
            rows.append({"strategy": name, "params": json.dumps(params),
                         "score": score, "full_sharpe": full.get("sharpe"),
                         "is_sharpe": st_is.get("sharpe"),
                         "oos_sharpe": st_oos.get("sharpe"),
                         "oos_cagr_pct": st_oos.get("cagr_pct"),
                         "oos_max_dd_pct": st_oos.get("max_dd_pct"),
                         "oos_pf": st_oos.get("profit_factor"),
                         "trade_days_full": full.get("trade_days"),
                         "quarters_hit": round(qh, 3),
                         "full_total_pnl": full.get("total_pnl")})
        print(f"[atlas] {name}: {len(combos)} combos")

    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    out = Path(out_dir) if out_dir else repo_root() / "results" / "atlas"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "atlas_report.csv", index=False)

    # greedy portfolio: survivors only, pairwise OOS corr cap
    survivors = [(r["strategy"] + "|" + r["params"], r) for r in rows
                 if r["score"] >= PROMOTE_THRESHOLD]
    chosen, chosen_pnls = [], {}
    for key, r in sorted(survivors, key=lambda kv: -kv[1]["score"]):
        p = oos_pnls[key]
        ok = True
        for ck in chosen:
            cp = chosen_pnls[ck]
            both = pd.concat([p, cp], axis=1).dropna()
            if len(both) > 30 and abs(float(both.corr().iloc[0, 1])) > 0.55:
                ok = False
                break
        if ok:
            chosen.append(key)
            chosen_pnls[key] = p
    combined = (pd.concat(chosen_pnls, axis=1).mean(axis=1)
                if chosen_pnls else pd.Series(dtype=float))
    port_stats = perf_stats(combined[combined != 0], capital) \
        if len(combined[(combined != 0)]) else {"days": 0}

    summary = {"generated": pd.Timestamp.now().isoformat(),
               "sessions": int(len(nifty_index)), "combos": int(len(df)),
               "promoted_count": int(len(survivors)),
               "portfolio_keys": chosen,
               "portfolio_oos": port_stats}
    (out / "atlas_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"[atlas] promoted {len(survivors)}/{len(df)} | "
          f"portfolio size {len(chosen)} | report -> {out}")
    return df
