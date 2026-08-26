import argparse
import os
import sys
from datetime import date, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.metrics import perf_stats, save_report
from src.backtest.real_engine import (RealEngine, detect_expiry_days, expiry_weekday_histogram,
                                      load_legs_by_wing, load_vix)
from src.config import Settings

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ.get("RESULTS_DIR", ROOT / "results"))
CAPITAL = 500000.0


def money_fmt(x):
    return f"Rs {float(x):,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-08-24")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--min-expiries", type=int, default=20)
    ap.add_argument("--wing", type=int, default=150)
    args = ap.parse_args()

    legs_by_wing = load_legs_by_wing()
    if not legs_by_wing:
        print("no Dhan data - run: .venv/bin/python scripts/dhan_backfill.py")
        sys.exit(1)
    wing = args.wing if args.wing in legs_by_wing else min(legs_by_wing, key=lambda w: abs(w - args.wing))
    print(f"[real-bt] wing {wing} (available wings: {sorted(legs_by_wing)})")

    expiries_all = detect_expiry_days(legs_by_wing[wing]["atm_ce"])
    if len(expiries_all) < args.min_expiries:
        print(f"[real-bt] expiry detection found only {len(expiries_all)} days - check data")
        sys.exit(1)
    start, end = pd.Timestamp(args.start).date(), pd.Timestamp(args.end).date()
    expiries = [d for d in expiries_all if start <= d <= end]
    print("[real-bt] detected expiry days per year (data-driven):")
    for y, parts in expiry_weekday_histogram(expiries_all).items():
        print(f"   {y}: {' '.join(parts)}")

    cfg = Settings.load().raw
    eng = cfg["strategies"]["iron_fly_expiry"]
    risk = cfg["risk"]
    entry_t = dtime.fromisoformat(eng["entry_time"])
    print(f"[real-bt] {len(expiries)} expiry days in window; rules: entry {entry_t}, "
          f"stop {eng['stop_mult']}x credit (value-based), keep {float(eng['target_keep_pct']):.0%}, "
          f"min credit {eng['min_credit_pts']}, VIX<={risk['vix_max_for_theta']}")

    engine = RealEngine({wing: legs_by_wing[wing]})
    b_ce, b_pe = engine.skew(wing, expiries)
    print(f"[real-bt] calibrated wing skew: CE {b_ce * 100:+.2f} vols, PE {b_pe * 100:+.2f} vols")
    min_credit_for_size = wing - (CAPITAL * float(risk["risk_per_trade_pct"]) / 100.0) / 65
    print(f"[real-bt] sizing note: 1 lot at Rs {CAPITAL:,.0f} needs credit >= {min_credit_for_size:.1f} pts")

    vix_by_date, vix_dates = load_vix()
    trades = engine.replay(expiries, wing=wing, entry_t=entry_t,
                           keep_pct=float(eng["target_keep_pct"]), stop_mult=float(eng["stop_mult"]),
                           min_credit=float(eng["min_credit_pts"]), max_vix=float(risk["vix_max_for_theta"]),
                           vix_by_date=vix_by_date, vix_dates=vix_dates, size=True, capital=CAPITAL, risk=risk)
    if trades.empty:
        print("no trades produced - check data coverage / filters")
        return

    pnl = trades["pnl"]
    st = perf_stats(pnl, CAPITAL)
    tot_bars = 0
    print("\n======= REAL-PRICE BACKTEST (Dhan expired options, minute-level) =======")
    print(f"window     : {expiries[0]} -> {expiries[-1]}")
    print(f"trades     : {len(trades)} | exits: {trades['reason'].value_counts().to_dict()}")
    print(f"avg credit : {trades['credit'].mean():.1f} pts | median lots {int(trades['lots'].median())}")
    print(f"CAGR       : {st['cagr_pct']}% | Sharpe {st['sharpe']} | MaxDD {st['max_dd_pct']}%")
    print(f"win rate   : {st['win_rate_pct']}% | PF {st['profit_factor']} | total {money_fmt(st['total_pnl'])}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    pnl.rename("combined").to_frame().assign(iron_fly=pnl, gap=0.0, overnight=0.0).to_csv(RESULTS / "daily_pnl.csv")
    save_report({"real_price_backtest": {"stats": st,
                 "note": "real premiums; fixed strikes; value-based stop; VIX filter; "
                         "BS-with-real-IV only for strikes the rolling anchor is not currently printing",
                 "wing": wing, "wing_skew_bump_vols": {"ce": round(b_ce * 100, 2), "pe": round(b_pe * 100, 2)},
                 "exits": trades["reason"].value_counts().to_dict()}},
                str(RESULTS / "real_price_report.json"))
    trades.to_csv(RESULTS / "real_trades.csv")
    print("\nsaved -> results/daily_pnl.csv, results/real_trades.csv, results/real_price_report.json")


if __name__ == "__main__":
    main()
