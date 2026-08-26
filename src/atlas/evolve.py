"""The evolution loop: enumerate candidate specs from the component library,
skip anything already in the ledger (nothing tested twice), evaluate on real
data with the house IS/OOS line, score with conviction(), and queue survivors
for paper trading. Each run appends to results/atlas/ledger.csv so the system
never forgets what it has already learned.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.atlas import components as C
from src.atlas.data_global import load_daily, load_global, repo_root, spx_overnight_map
from src.atlas.engine_daily import run_spec
from src.backtest.metrics import perf_stats
from src.config import Settings
from src.costs import CostModel
from src.research.conviction import PROMOTE_THRESHOLD, conviction_score, quarter_consistency

OOS_START = pd.Timestamp("2024-01-01")


def spec_hash(family: str, params: dict, filt: str, exit_spec: tuple) -> str:
    raw = json.dumps([family, params, filt, list(exit_spec)], sort_keys=True)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:12]


def enumerate_specs() -> list[dict]:
    specs = []
    for family, (builder, param_list) in C.SIGNALS.items():
        for params, filt, exit_spec in itertools.product(
                param_list, C.FILTERS, C.EXITS):
            specs.append({"family": family, "params": params, "filter": filt,
                          "exit": exit_spec})
    return specs


def evaluate_spec(aux, spec, lot, cost_fn) -> tuple[list[dict], float, float | None]:
    builder = C.SIGNALS[spec["family"]][0]
    long_m, short_m = builder(aux, spec["params"])
    gate = C.FILTERS[spec["filter"]](aux, spec["params"])
    long_m = np.asarray(long_m, dtype=bool) & gate
    short_m = np.asarray(short_m, dtype=bool) & gate
    trades = run_spec(aux, long_m, short_m, spec["exit"], lot, cost_fn)
    return trades


def pnl_series(trades: list[dict], index: pd.DatetimeIndex) -> pd.Series:
    if not trades:
        return pd.Series(0.0, index=index)
    s = pd.Series({pd.Timestamp(t["date"]): t["pnl"] for t in trades})
    s = s.groupby(level=0).sum().sort_index()
    return s.reindex(index.normalize()).fillna(0.0)


def run_generation(max_specs: int | None = None,
                   out_dir: str | Path | None = None) -> pd.DataFrame:
    cfg = Settings.load().raw
    capital = float(cfg.get("capital", 500_000))
    cm = CostModel.from_cfg(cfg["costs"])
    lot = int(cfg["universe"]["lot_size"])
    risk = {"fixed_lots": 1}

    daily = load_daily("nifty")
    vix = load_daily("indiavix")["close"]
    spx_on = spx_overnight_map(daily.index, load_global("spx", refresh=False))
    banknifty = load_daily("banknifty")
    aux = C.build_aux(daily, vix, spx_on, banknifty)
    index = daily.index

    out = Path(out_dir) if out_dir else repo_root() / "results" / "atlas"
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / "ledger.csv"
    seen: set[str] = set()
    if ledger_path.exists():
        old = pd.read_csv(ledger_path)
        seen = set(old["spec_hash"])

    specs = enumerate_specs()
    fresh = [s for s in specs
             if spec_hash(s["family"], s["params"], s["filter"], s["exit"]) not in seen]
    if max_specs:
        fresh = fresh[:max_specs]
    print(f"[evolve] {len(specs)} total specs | {len(seen)} in ledger | "
          f"{len(fresh)} new this generation")

    rows = []
    for j, spec in enumerate(fresh):
        trades = evaluate_spec(aux, spec, lot, cm.futures_round_trip)
        pnl = pnl_series(trades, index)
        nz = pnl[pnl != 0]
        full = perf_stats(nz.groupby(level=0).sum(), capital) if len(nz) else {"days": 0}
        oos_nz = pnl[(pnl.index >= OOS_START) & (pnl != 0)]
        st_oos = perf_stats(oos_nz.groupby(level=0).sum(), capital) if len(oos_nz) else {"days": 0}
        is_nz = pnl[(pnl.index < OOS_START) & (pnl != 0)]
        st_is = perf_stats(is_nz.groupby(level=0).sum(), capital) if len(is_nz) else {"days": 0}
        qh = quarter_consistency(pnl[(pnl.index >= OOS_START) & (pnl != 0)]) if len(oos_nz) else 0.0

        h = spec_hash(spec["family"], spec["params"], spec["filter"], spec["exit"])
        rows.append({
            "spec_hash": h, "family": spec["family"],
            "params": json.dumps(spec["params"]), "filter": spec["filter"],
            "exit": "_".join(map(str, spec["exit"])),
            "score": conviction_score(st_oos if st_oos.get("days") else full, None, qh),
            "full_sharpe": full.get("sharpe"), "is_sharpe": st_is.get("sharpe"),
            "oos_sharpe": st_oos.get("sharpe"),
            "oos_cagr_pct": st_oos.get("cagr_pct"),
            "oos_max_dd_pct": st_oos.get("max_dd_pct"),
            "oos_pf": st_oos.get("profit_factor"),
            "n_trades": full.get("trade_days", 0),
            "quarters_hit": round(qh, 3),
            "total_pnl": full.get("total_pnl"),
            "promoted": False,
        })
        if (j + 1) % 100 == 0:
            print(f"[evolve] {j + 1}/{len(fresh)}")

    df = pd.DataFrame(rows)
    if len(df):
        # plateau: conviction recomputed against family-level survival rate
        fam_rate = df.groupby("family")["total_pnl"].apply(lambda x: float(np.mean([p > 0 for p in x])))
        df["score"] = df.apply(
            lambda r: round(min(1.0, r["score"] * (0.7 + 0.6 * fam_rate[r["family"]])), 4),
            axis=1)
        df = df.sort_values("score", ascending=False)
        df["promoted"] = df["score"] >= PROMOTE_THRESHOLD

        if ledger_path.exists():
            old = pd.read_csv(ledger_path)
            df = pd.concat([old, df], ignore_index=True)
        df.to_csv(ledger_path, index=False)

        promoted = df[df["promoted"]]
        queue = promoted.to_dict("records")
        (out / "paper_queue.json").write_text(json.dumps(
            {"generated": pd.Timestamp.now().isoformat(),
             "queue": queue}, indent=2, default=str))
        print(f"[evolve] promoted {int(df['promoted'].sum())}/{len(rows)} new specs "
              f"-> paper_queue.json")
    else:
        print("[evolve] nothing new to test — all specs already in ledger")
    return df


if __name__ == "__main__":
    run_generation()
