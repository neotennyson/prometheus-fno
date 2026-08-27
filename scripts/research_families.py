"""Family-level real-price research on Nifty INDEX minutes.

Families (all params a-priori or walk-forward selected, stated per family):
  A. overnight drift + regime conditionals (vix level x vix-rising)
  B. range-expansion continuation (quiet n days, then k*ATR day -> ride into close)
  C. low-vol grind (vix<thr & >50dma -> afternoon long)
  D. turn-of-month (buy TD-1 close, hold first 3 sessions)
  E. expiry-morning momentum (expiry days only: follow 09:15-10:00 move to close)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from scripts.validate_gap_overnight import DL, FEE_BPS, SLIP_PTS, ist
from src.backtest.real_engine import detect_expiry_days, load_leg

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
TRAIN, TEST, STEP, MIN_TRADES = 548, 183, 183, 8


def entry_exit(day: pd.DataFrame, e_hm: str, x_hm: str, long: bool) -> float | None:
    e = day.between_time(e_hm, e_hm)
    x = day.between_time(x_hm, "15:26")
    if x.empty:
        x = day.iloc[-1:]
    if e.empty or x.empty:
        return None
    buy, sell = float(e["close"].iloc[0]), float(x["close"].iloc[-1])
    fee = FEE_BPS / 1e4 + 2 * SLIP_PTS / buy
    r = (sell - buy) / buy if long else (buy - sell) / buy
    return r - fee


def fam_overnight(daily, mday, vix_series, vix_min: float, rising: bool) -> pd.Series:
    v = vix_series.shift(1)
    v5 = vix_series.shift(6)
    rets = {}
    for i in range(1, len(daily) - 1):
        d, nxt = daily.index[i], daily.index[i + 1]
        vp = v.get(d)
        if vp is None or (isinstance(vp, float) and pd.isna(vp)) or vp < vix_min:
            continue
        v5 = vix_series.shift(6).get(d)
        if rising and not (vp > v5):
            continue
        day, nday = mday.get(d.date()), mday.get(nxt.date())
        if day is None or nday is None:
            continue
        # overnight = buy today near close, sell tomorrow near open
        e = day.between_time("15:22", "15:25")
        x = nday.between_time("09:25", "09:25")
        if x.empty:
            x = nday.iloc[:1]
        if e.empty or x.empty:
            continue
        buy, sell = float(e["close"].iloc[0]), float(x["close"].iloc[0])
        rets[pd.Timestamp(nxt)] = (sell - buy) / buy - (FEE_BPS / 1e4 + 2 * SLIP_PTS / buy)
    return pd.Series(rets).sort_index()


def fam_range_expansion(daily, mday, quiet_n: int, k_atr: float, e_hm: str) -> pd.Series:
    """Yesterday quiet (TR <= 1.15x trailing min), then this MORNING expands:
    |open->entry move| >= k_atr/4 x ATR14 by entry bar -> ride that direction into close."""
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - daily["close"].shift()).abs(),
                    (daily["low"] - daily["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().shift(1)
    quiet_ok = (tr <= 1.15 * tr.rolling(quiet_n).min()).shift(1)
    pc = daily["close"].shift(1)
    rets = {}
    for d, row in daily.iterrows():
        a, qk = atr.get(d), quiet_ok.get(d)
        if pd.isna(a) or pd.isna(qk) or a <= 0 or not qk:
            continue
        day = mday.get(d.date())
        if day is None or len(day) < 30:
            continue
        e = day.between_time(e_hm, e_hm)
        if e.empty:
            continue
        px = float(e["close"].iloc[0])
        move = px / float(row["open"]) - 1.0
        prev = pc.get(d)
        if pd.isna(prev) or abs(move) < k_atr * a / 4.0 / prev:
            continue
        dirn = 1.0 if move > 0 else -1.0
        fee = FEE_BPS / 1e4 + 2 * SLIP_PTS / px
        rets[pd.Timestamp(d)] = dirn * (float(day["close"].iloc[-1]) - px) / px - fee
    return pd.Series(rets).sort_index()


def fam_lowvol_grind(daily, mday, vix_series, vix_thr: float) -> pd.Series:
    """Prev session: VIX<thr AND prev close>50dma -> afternoon long drift capture."""
    sma50 = daily["close"].rolling(50).mean()
    above_prev = (daily["close"] > sma50).shift(1)
    vprev = vix_series.shift(1)
    rets = {}
    for d, row in daily.iterrows():
        ab, vp = above_prev.get(d), vprev.get(d)
        if not (ab is True) or pd.isna(vp) or vp >= vix_thr:
            continue
        day = mday.get(d.date())
        if day is None or len(day) < 30:
            continue
        r = entry_exit(day, "14:30", "15:25", long=True)
        if r is not None:
            rets[pd.Timestamp(d)] = r
    return pd.Series(rets).sort_index()


def fam_turn_of_month(daily, mday, hold_days: int = 3) -> pd.Series:
    """Buy at last session's close-1 bar of the month... classic TOM: enter close of
    TD-1 (second-to-last trading day), exit close of +hold_days-th session."""
    months = daily.index.to_period("M")
    dates = list(daily.index)
    rets = {}
    for i, d in enumerate(dates[:-hold_days - 1]):
        if months[i] == months[i + hold_days] and (i + hold_days + 1 >= len(dates) or months[i + hold_days + 1] != months[i]):
            exit_d_idx = i + hold_days          # last session of the month
            entry_d_idx = exit_d_idx - hold_days
            ed, xd = dates[entry_d_idx], dates[exit_d_idx]
            day_e, day_x = mday.get(ed.date()), mday.get(xd.date())
            if day_e is None or day_x is None:
                continue
            e = day_e.between_time("15:20", "15:25")
            x = day_x.between_time("15:20", "15:25")
            if e.empty or x.empty:
                continue
            buy, sell = float(e["close"].iloc[-1]), float(x["close"].iloc[-1])
            rets[pd.Timestamp(xd)] = (sell - buy) / buy - (FEE_BPS / 1e4 + 2 * SLIP_PTS / buy)
    return pd.Series(rets).sort_index()


def fam_expiry_momentum(daily, mday, expiries, thr_frac: float) -> pd.Series:
    exp_set = {pd.Timestamp(d) for d in expiries}
    pc = daily["close"].shift(1)
    atr_num = (daily["high"] - daily["low"]).rolling(14).mean().shift(1)
    rets = {}
    for d, row in daily.iterrows():
        if d not in exp_set:
            continue
        a, prev = atr_num.get(d), pc.get(d)
        if pd.isna(a) or pd.isna(prev) or a <= 0:
            continue
        day = mday.get(d.date())
        if day is None or len(day) < 300:
            continue
        m00 = day.between_time("09:15", "10:00")
        if m00.empty:
            continue
        move = m00["close"].iloc[-1] / m00["close"].iloc[0] - 1.0
        if abs(move) < thr_frac * a / prev:
            continue
        dirn = 1.0 if move > 0 else -1.0
        entry_px = float(m00["close"].iloc[-1])
        fee = FEE_BPS / 1e4 + 2 * SLIP_PTS / entry_px
        rets[pd.Timestamp(d)] = dirn * (float(day["close"].iloc[-1]) - entry_px) / entry_px - fee
    return pd.Series(rets).sort_index()


def wf(series: dict, start, end, tag: str):
    oos, choices = {}, []
    w = start
    while w + pd.Timedelta(days=TRAIN + TEST) <= end:
        tr_a, tr_b = w, w + pd.Timedelta(days=TRAIN)
        te_a, te_b = tr_b, tr_b + pd.Timedelta(days=TEST)
        best, bs = None, -9.0
        for key, s in series.items():
            tr = s[(s.index >= tr_a) & (s.index < tr_b)]
            if len(tr) < MIN_TRADES or tr.std(ddof=0) == 0:
                continue
            sh = float(tr.mean() / tr.std(ddof=0) * np.sqrt(252))
            if sh > bs:
                bs, best = sh, key
        if best is not None:
            te = series[best][(series[best].index >= te_a) & (series[best].index < te_b)]
            for dd, r in te.items():
                oos[dd] = oos.get(dd, 0.0) + r
            choices.append({"test": f"{te_a.date()}->{te_b.date()}", "chose": str(best),
                            "train_sharpe": round(bs, 2), "oos_n": len(te),
                            "oos_sum_pct": round(te.sum() * 100, 2)})
            print(f"  [{tag}] {te_a.date()}->: chose {best} (Sh {bs:.2f}) | OOS {len(te)} tr, {te.sum()*100:+.2f}%")
        w += pd.Timedelta(days=STEP)
    return pd.Series(list(oos.values()), index=pd.DatetimeIndex(list(oos.keys()))).sort_index(), choices


def stats(s: pd.Series, tag: str) -> dict:
    if s.empty:
        print(f"{tag}: EMPTY")
        return {}
    eq = (1 + s).cumprod()
    sh = float(s.mean() / s.std(ddof=0) * np.sqrt(252)) if s.std(ddof=0) > 0 else 0.0
    out = {"n": int(len(s)), "win": round(100 * (s > 0).mean(), 1), "total": round(100 * (eq.iloc[-1] - 1), 2),
           "sharpe": round(sh, 2), "maxdd": round(100 * (eq / eq.cummax() - 1).min(), 1)}
    print(f"{tag}: n={out['n']} | win {out['win']}% | total {out['total']:+.2f}% | Sharpe {out['sharpe']} | MaxDD {out['maxdd']}%")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    args = ap.parse_args()

    daily = pd.read_parquet(DL / "nifty_index_daily.parquet")
    daily.index = pd.to_datetime(daily.index)
    daily = daily[daily.index >= args.start]
    mins = ist(pd.read_parquet(DL / "nifty_index_minute.parquet"))
    mdate = pd.Series(mins.index.date, index=mins.index)
    mday = {d: g for d, g in mins.groupby(mdate)}
    vix_df = pd.read_parquet(ROOT / "data" / "raw" / "indiavix_daily.parquet")["close"]
    vs = pd.Series(vix_df.values, index=pd.to_datetime(vix_df.index).normalize())
    expiries = detect_expiry_days(load_leg("CALL_ATM"))
    print(f"[fam] {len(expiries)} expiry days from option data")

    start, end = daily.index[0], daily.index[-1]

    print("\n--- diagnostics: overnight by VIX direction (fix-2026 hunt) ---")
    base_oni = {}
    for label, kwargs in [("v>=17 fall", dict(vix_min=17.0, rising=False)),
                          ("v>=17 rise", dict(vix_min=17.0, rising=True))]:
        s = fam_overnight(daily, mday, vs, **kwargs)
        if s.empty:
            print(f"{label}: EMPTY")
            continue
        yr = s.groupby(s.index.year).agg(["count", "sum"])
        seg = " | ".join(f"{y}: {r['sum']*100:+.1f}% (n={int(r['count'])})" for y, r in yr.iterrows())
        print(f"{label}: {seg}")
        base_oni[label] = s

    families = {
        "overnight (6 variants)": {(k["vix_min"], k["rising"]): fam_overnight(daily, mday, vs, **k)
                                   for k in [dict(vix_min=v, rising=r) for v in (14., 17., 20.) for r in (False, True)]},
        "range_expansion": {(q, k, e): fam_range_expansion(daily, mday, q, k, e)
                            for q in (15, 20) for k in (1.3, 1.6) for e in ("10:30",)},
        "lowvol_grind": {t: fam_lowvol_grind(daily, mday, vs, t) for t in (13., 15.)},
        "turn_of_month": {"tom3": fam_turn_of_month(daily, mday, 3)},
        "expiry_momentum": {t: fam_expiry_momentum(daily, mday, expiries, t) for t in (0.35, 0.5)},
    }

    ooses, all_choices = {}, {}
    for name, grid in families.items():
        print(f"\n=== walk-forward: {name} ===")
        oos, ch = wf(grid, start, end, name.split()[0])
        ooses[name.split()[0]] = oos
        all_choices[name] = ch

    print("\n=========== STITCHED OOS BY FAMILY ===========")
    survivors = {}
    for name, oos in ooses.items():
        st = stats(oos, f"{name:18s}")
        if st and st["sharpe"] > 0.4 and st["total"] > 0:
            survivors[name] = oos

    if survivors:
        combo = pd.concat(list(survivors.values()) + [], axis=1).fillna(0).sum(axis=1).sort_index()
        print(f"\nsurvivors combined ({list(survivors)}):")
        stats(combo, "SURVIVORS COMBO ")
        yr = combo.groupby(combo.index.year).agg(["count", "sum"])
        for y, r in yr.iterrows():
            print(f"    {y}: n={int(r['count'])}, {r['sum']*100:+.2f}%")

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({k: v for k, v in ooses.items()}).fillna(0.0).to_csv(RESULTS / "families_oos_daily.csv")
    with open(RESULTS / "families_wf_choices.json", "w") as f:
        import json
        json.dump(all_choices, f, indent=1)
    print(f"\nsaved -> results/families_oos_daily.csv, results/families_wf_choices.json")


if __name__ == "__main__":
    main()
