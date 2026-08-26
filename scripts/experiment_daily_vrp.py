"""EXPERIMENT: can VRP harvest (the engine behind iron_fly_expiry) run EVERY day
instead of expiry-Tuesdays only? Uses real Dhan 1-min option panels, full cost stack,
same conservative fill assumptions as run_real_backtest.py. No synthetic prices.

Variants tested:
  A) iron fly every weekday, VIX<=22 gate          (theta + gap-risk both sides defined)
  B) put-credit-spread style proxy is NOT testable without dedicated leg files;
     we approximate variant B by running the fly only Mon/Wed/Fri for comparison.
Output: results/research/daily_vrp_report.json
"""
from __future__ import annotations

import json
from datetime import date, time as dtime
from pathlib import Path

import pandas as pd

from src.backtest.metrics import perf_stats, save_report
from src.backtest.real_engine import RealEngine, load_legs_by_wing, load_vix
from src.config import Settings
from src.data.calendar import all_holidays, weekly_expiry_on_or_after


def main():
    settings = Settings.load().raw
    cap = float(settings["capital"])
    legs = load_legs_by_wing()
    if not legs:
        raise SystemExit("No Dhan option legs found.")
    vix_by_date, vix_dates = load_vix()
    hol = all_holidays()

    def expiry_of(d):
        exp = weekly_expiry_on_or_after(d, hol)
        return pd.Timestamp(exp) + pd.Timedelta(hours=15, minutes=30)

    atm = legs[100]["atm_ce"]
    all_days = sorted(set(atm["date"]))

    out = {}
    for label, wing in ((f"fly_{w}", w) for w in sorted(legs)):
        engine = RealEngine(legs_by_wing={wing: legs[wing]}, exit_t=dtime(15, 18))
        for tag in ("all_days", "non_expiry_only"):
            days = [d for d in all_days
                    if d.weekday() != 1] if tag == "non_expiry_only" else all_days
            tr = engine.replay(days, wing, dtime(10, 5), keep_pct=0.40,
                               stop_mult=2.0, min_credit=55,
                               max_vix=float(settings["risk"]["vix_max_for_theta"]),
                               vix_by_date=vix_by_date, vix_dates=vix_dates,
                               size=False, expiry_of=expiry_of)
            st = perf_stats(tr["pnl"], cap) if len(tr) else {"days": 0}
            out[f"{label}:{tag}"] = st
            print(label, tag, "->", {k: st.get(k) for k in
                                     ("trade_days", "cagr_pct", "sharpe",
                                      "max_dd_pct", "win_rate_pct")})

    p = Path("results/research")
    p.mkdir(parents=True, exist_ok=True)
    save_report(out, str(p / "daily_vrp_report.json"))
    print("saved ->", p / "daily_vrp_report.json")


if __name__ == "__main__":
    main()
