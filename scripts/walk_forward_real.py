import argparse
import itertools
import os
import sys
from datetime import date, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.metrics import perf_stats, save_report
from src.backtest.real_engine import (RealEngine, daily_sharpe, detect_expiry_days,
                                      expiry_weekday_histogram, load_legs_by_wing,
                                      load_vix)
from src.config import Settings

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ.get("RESULTS_DIR", ROOT / "results"))

ENTRY_TIMES = [dtime(10, 5), dtime(11, 35), dtime(12, 50)]
KEEPS = [0.30, 0.40, 0.50]
STOPS = [1.5, 2.0, 3.0, None]
CREDITS = [55.0, 70.0]
TRAIN_DAYS, TEST_DAYS, STEP_DAYS = 548, 183, 183
MIN_TRAIN_TRADES = 8
MAX_VIX = 22.0


def fmt_t(t):
    return t.strftime("%H:%M")


def fmt_params(p):
    stop = f"{p['stop_mult']}x" if p["stop_mult"] else "off"
    return f"{fmt_t(p['entry_t'])}/w{p['wing']}/k{int(p['keep_pct']*100)}/s{stop}/c{int(p['min_credit'])}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-08-24")
    ap.add_argument("--end", default=str(date.today()))
    args = ap.parse_args()

    legs_by_wing = load_legs_by_wing()
    if not legs_by_wing:
        print("no Dhan data found — run scripts/dhan_backfill.py first")
        sys.exit(1)
    wings = sorted(legs_by_wing)
    print(f"[wf-real] wings available: {wings}")

    expiries_all = detect_expiry_days(legs_by_wing[wings[0]]["atm_ce"])
    start, end = pd.Timestamp(args.start).date(), pd.Timestamp(args.end).date()
    expiries_all = [d for d in expiries_all if start <= d <= end]
    print(f"[wf-real] {len(expiries_all)} expiry days {expiries_all[0]} -> {expiries_all[-1]}")
    print("[wf-real] expiry weekdays per year:", {y: " ".join(v) for y, v in expiry_weekday_histogram(expiries_all).items()})

    engine = RealEngine(legs_by_wing)
    vix_by_date, vix_dates = load_vix()
    cfg = Settings.load().raw
    risk = cfg["risk"]

    grid = [dict(entry_t=e, wing=w, keep_pct=k, stop_mult=s, min_credit=c)
            for e, w, k, s, c in itertools.product(ENTRY_TIMES, wings, KEEPS, STOPS, CREDITS)]
    baseline = dict(entry_t=dtime(10, 5), wing=150 if 150 in wings else wings[len(wings) // 2],
                    keep_pct=0.40, stop_mult=2.0, min_credit=55.0)
    print(f"[wf-real] grid: {len(grid)} combos | baseline: {fmt_params(baseline)}")

    windows = []
    w_start = expiries_all[0]
    while w_start + timedelta(days=TRAIN_DAYS + TEST_DAYS) <= expiries_all[-1]:
        tr_end = w_start + timedelta(days=TRAIN_DAYS)
        te_end = tr_end + timedelta(days=TEST_DAYS)
        windows.append((w_start, tr_end, te_end))
        w_start += timedelta(days=STEP_DAYS)
    print(f"[wf-real] {len(windows)} walk-forward windows (18mo train / 6mo test)\n")

    rows = []
    oos_opt, oos_base = {}, {}
    for i, (ws, te_split, te_end) in enumerate(windows, 1):
        train = [d for d in expiries_all if ws <= d <= te_split]
        test = [d for d in expiries_all if te_split < d <= te_end]
        best, best_score = None, -1e9
        for p in grid:
            tr = engine.replay(train, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates, **p)
            if len(tr) < MIN_TRAIN_TRADES:
                continue
            score = daily_sharpe(tr, ws, te_split)
            if score > best_score:
                best_score, best = score, p
        if best is None:
            best, best_score = baseline, float("nan")
        tr_best = engine.replay(train, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates, **best)
        te = engine.replay(test, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates, **best)
        te_b = engine.replay(test, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates, **baseline)
        s_te, s_base = daily_sharpe(te, te_split, te_end), daily_sharpe(te_b, te_split, te_end)
        for d, r in te["pnl"].items():
            oos_opt[d] = oos_opt.get(d, 0.0) + r
        for d, r in te_b["pnl"].items():
            oos_base[d] = oos_base.get(d, 0.0) + r
        rows.append({"window": f"W{i}", "train": f"{ws}->{te_split}", "test": f"{te_split}->{te_end}",
                     "params": fmt_params(best), "train_sharpe": round(best_score, 2),
                     "train_trades": len(tr_best), "train_pnl": round(float(tr_best["pnl"].sum()), 0),
                     "test_trades": len(te), "test_pnl": round(float(te["pnl"].sum()), 0),
                     "test_sharpe": round(s_te, 2),
                     "base_test_pnl": round(float(te_b["pnl"].sum()), 0), "base_test_sharpe": round(s_base, 2)})
        r = rows[-1]
        print(f"{r['window']} train {r['train']} | chose {r['params']} (train Sh {r['train_sharpe']}, "
              f"{r['train_trades']} trades) | OOS: {r['test_trades']} trades, {r['test_pnl']:+.0f}, "
              f"Sh {r['test_sharpe']} | base OOS: {r['base_test_pnl']:+.0f}, Sh {r['base_test_sharpe']}")

    df = pd.DataFrame(rows)
    opt_ser = pd.Series(oos_opt).sort_index()
    base_ser = pd.Series(oos_base).sort_index()
    st_opt, st_base = perf_stats(opt_ser, 500000.0), perf_stats(base_ser, 500000.0)
    n_profit = int((df["test_pnl"] > 0).sum())

    print("\n=========== WALK-FORWARD (REAL PRICES) — AGGREGATE ===========")
    print(f"OOS windows profitable : {n_profit}/{len(df)}")
    print(f"optimized OOS  : total {opt_ser.sum():+,.0f} | Sharpe {st_opt['sharpe']} | "
          f"win {st_opt['win_rate_pct']}% | MaxDD {st_opt['max_dd_pct']}%")
    print(f"baseline OOS   : total {base_ser.sum():+,.0f} | Sharpe {st_base['sharpe']} | "
          f"win {st_base['win_rate_pct']}% | MaxDD {st_base['max_dd_pct']}%")
    chosen = df["params"].value_counts()
    print(f"params chosen  : {chosen.to_dict()}")
    print("note: OOS windows stitch across different param sets — this IS the honest live-like result")

    RESULTS.mkdir(parents=True, exist_ok=True)
    save_report({"walk_forward_real": {
        "windows": rows,
        "aggregate": {"optimized": st_opt, "baseline": st_base,
                      "profitable_windows": f"{n_profit}/{len(df)}",
                      "params_chosen": chosen.to_dict()},
        "grid": {"entry": [fmt_t(t) for t in ENTRY_TIMES], "wings": wings, "keep": KEEPS,
                 "stop": STOPS, "min_credit": CREDITS}}},
        str(RESULTS / "walk_forward_real.json"))
    df.to_csv(RESULTS / "walk_forward_real_windows.csv", index=False)
    pd.DataFrame({"optimized": opt_ser, "baseline": base_ser}).fillna(0).to_csv(RESULTS / "oos_daily_pnl.csv")
    print(f"\nsaved -> results/walk_forward_real.json, results/walk_forward_real_windows.csv, results/oos_daily_pnl.csv")


if __name__ == "__main__":
    main()
