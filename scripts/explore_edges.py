import sys
from datetime import date, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.backtest.metrics import perf_stats
from src.backtest.real_engine import (RealEngine, detect_expiry_days, load_legs_by_wing,
                                      load_vix, vix_prev_close)

CAP = 500_000.0
MAX_VIX = 22.0


def stats(trades, label, split=None):
    if trades.empty:
        print(f"  {label:34s} no trades")
        return
    st = perf_stats(trades["pnl"], CAP)
    extra = ""
    if split:
        pre = trades[trades.index < pd.Timestamp(split)]["pnl"]
        post = trades[trades.index >= pd.Timestamp(split)]["pnl"]
        extra = f" | IS {pre.sum():+8.0f}  OOS {post.sum():+8.0f}"
    print(f"  {label:34s} n={st['trade_days']:3d} win={st['win_rate_pct']:5.1f}% "
          f"PF={st['profit_factor']:5.2f} total={st['total_pnl']:+9.0f} Sh={st['sharpe']:+5.2f}{extra}")


def main():
    legs = load_legs_by_wing()
    engine = RealEngine(legs)
    expiries = detect_expiry_days(legs[150]["atm_ce"])
    vix_by_date, vix_dates = load_vix()

    print("warming engine caches (wing 150)...")
    feats = {}
    for d in expiries:
        vp = vix_prev_close(vix_by_date, d, vix_dates)
        if vp is None or vp > MAX_VIX:
            continue
        p = engine._path(150, d, dtime(10, 5))
        if p is None:
            continue
        day = engine._day(150, d)
        panel = day["panel"]
        pre = panel[panel.index.time < dtime(10, 5)]
        if pre.empty:
            continue
        open_spot = float(pre["spot"].iloc[0])
        entry_spot = float(panel.loc[panel.index >= pd.Timestamp(d) + pd.Timedelta(hours=10, minutes=5), "spot"].iloc[0])
        iv_entry = float(day["iv"].loc[day["iv"].index.time >= dtime(10, 5)].iloc[0])
        feats[d] = {"iv": iv_entry, "morning_move": abs(entry_spot - open_spot) / open_spot}

    fdf = pd.DataFrame(feats).T.sort_index()
    fdf["iv_trail"] = fdf["iv"].rolling(8, min_periods=4).mean().shift(1)
    days = fdf.index.tolist()
    split = days[len(days) // 2]
    print(f"{len(days)} tradeable expiry days, split at {split}\n")

    def run(label, day_list, **kw):
        t = engine.replay(day_list, wing=150, entry_t=dtime(10, 5), keep_pct=kw.pop("keep", 0.40),
                          stop_mult=kw.pop("stop", 2.0), min_credit=kw.pop("credit", 55.0),
                          max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates, **kw)
        stats(t, label, split)
        return t

    print("A) entry-timing & exit variants (all days):")
    run("base 10:05 k40 s2.0", days)
    run("late entry 11:35", days, entry_t=None) if False else None
    t = engine.replay(days, wing=150, entry_t=dtime(11, 35), keep_pct=0.40, stop_mult=2.0,
                      min_credit=55.0, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates)
    stats(t, "late entry 11:35 k40 s2.0", split)
    t = engine.replay(days, wing=150, entry_t=dtime(10, 5), keep_pct=0.40, stop_mult=None,
                      min_credit=55.0, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates)
    stats(t, "no stop (hold to EOD)", split)
    eng_early = RealEngine(legs, exit_t=dtime(13, 50))
    t = eng_early.replay(days, wing=150, entry_t=dtime(10, 5), keep_pct=0.40, stop_mult=2.0,
                         min_credit=55.0, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates)
    stats(t, "time-stop 13:50", split)

    print("\nB) day filters (base exits):")
    calm = [d for d in days if feats[d]["morning_move"] < 0.0025]
    run(f"calm morning (move<0.25%) n={len(calm)}", calm)
    high_iv = [d for d in days if fdf.loc[d, "iv_trail"] == fdf.loc[d, "iv_trail"] and fdf.loc[d, "iv"] > fdf.loc[d, "iv_trail"]]
    run(f"IV above trailing avg n={len(high_iv)}", high_iv)
    both = [d for d in high_iv if feats[d]["morning_move"] < 0.0025]
    run(f"calm + IV-high n={len(both)}", both)

    print("\nC) trade the day BEFORE expiry (T-1, theta without gamma):")
    all_days = sorted(set(legs[150]["atm_ce"]["date"]))
    t1 = []
    for d in expiries:
        prev = [x for x in all_days if x < d]
        if prev:
            t1.append(prev[-1])
    t1 = [d for d in t1 if vix_prev_close(vix_by_date, d, vix_dates) is not None
          and vix_prev_close(vix_by_date, d, vix_dates) <= MAX_VIX]
    t1 = engine.replay(t1, wing=150, entry_t=dtime(10, 5), keep_pct=0.40, stop_mult=2.0,
                       min_credit=55.0, max_vix=MAX_VIX, vix_by_date=vix_by_date, vix_dates=vix_dates)
    stats(t1, "iron fly on T-1", split)


if __name__ == "__main__":
    main()
