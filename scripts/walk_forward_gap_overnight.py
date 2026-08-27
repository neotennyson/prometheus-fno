import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from scripts.validate_gap_overnight import DL, FEE_BPS, SLIP_PTS, ist

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
TRAIN, TEST, STEP = 548, 183, 183
MIN_TRADES = 8


def build_gap_series(daily: pd.DataFrame, mins: pd.DataFrame, k_atr: float, cont: bool) -> pd.Series:
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - daily["close"].shift()).abs(),
                    (daily["low"] - daily["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().shift(1)
    pc = daily["close"].shift(1)
    mdate = pd.Series(mins.index.date, index=mins.index)
    mday = {d: g for d, g in mins.groupby(mdate)}
    rets = {}
    for d, row in daily.iterrows():
        a, prev = atr.get(d), pc.get(d)
        if pd.isna(a) or pd.isna(prev) or a <= 0:
            continue
        gap = row["open"] / prev - 1.0
        if abs(gap) < k_atr * a / prev:
            continue
        day = mday.get(d.date())
        if day is None or len(day) < 30:
            continue
        entry = float(day["close"].iloc[0])
        sign = (1.0 if gap > 0 else -1.0) if cont else (-1.0 if gap > 0 else 1.0)
        path = sign * (day["close"] - entry) / entry
        rets[pd.Timestamp(d)] = float(path.iloc[-1]) - (FEE_BPS / 1e4 + 2 * SLIP_PTS / entry)
    return pd.Series(rets).sort_index()


def build_overnight_series(daily: pd.DataFrame, mday: dict, vix: dict, vix_min: float,
                           entry_hm: str = "15:22", exit_hm: str = "09:25") -> pd.Series:
    dates = sorted(daily.index)
    rets = {}
    for i, d in enumerate(dates[:-1]):
        v = vix.get(d)
        if v is None or v < vix_min:
            continue
        nxt = dates[i + 1]
        day, nday = mday.get(d.date()), mday.get(nxt.date())
        if day is None or nday is None:
            continue
        e = day.between_time(entry_hm, "15:25")
        x = nday.between_time(exit_hm, exit_hm)
        if x.empty:
            x = nday.iloc[:1]
        if e.empty or x.empty:
            continue
        buy, sell = float(e["close"].iloc[0]), float(x["close"].iloc[0])
        rets[pd.Timestamp(nxt)] = (sell - buy) / buy - (FEE_BPS / 1e4 + 2 * SLIP_PTS / buy)
    return pd.Series(rets).sort_index()


def wf(series: dict, start, end, tag: str):
    """Walk-forward: pick best variant on train Sharpe, stitch its test returns."""
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
        if best is None:
            w += pd.Timedelta(days=STEP)
            continue
        te = series[best][(series[best].index >= te_a) & (series[best].index < te_b)]
        for d, r in te.items():
            oos[d] = oos.get(d, 0.0) + r
        choices.append((str(te_a.date()), best, round(bs, 2), len(te), round(te.sum() * 100, 2)))
        print(f"  [{tag}] {te_a.date()}->{te_b.date()}: chose {best} (train Sh {bs:.2f}) "
              f"| OOS n={len(te)}, {te.sum()*100:+.2f}%")
        w += pd.Timedelta(days=STEP)
    return pd.Series(oos).sort_index(), choices


def stats(s: pd.Series, tag: str):
    if s.empty:
        print(f"{tag}: EMPTY")
        return {}
    eq = (1 + s).cumprod()
    sh = float(s.mean() / s.std(ddof=0) * np.sqrt(252)) if s.std(ddof=0) > 0 else 0.0
    out = {"n": len(s), "win": 100 * (s > 0).mean(), "total": 100 * (eq.iloc[-1] - 1),
           "sharpe": sh, "maxdd": 100 * (eq / eq.cummax() - 1).min()}
    print(f"{tag}: n={out['n']} | win {out['win']:.1f}% | total {out['total']:+.2f}% "
          f"| Sharpe {out['sharpe']:.2f} | MaxDD {out['maxdd']:.1f}%")
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
    vix = {pd.Timestamp(k): v for k, v in zip(pd.to_datetime(vix_df.index).date, vix_df.values)}

    print("building series...")
    gap = {(k, c): build_gap_series(daily, mins, k, c) for k in (0.5, 0.75, 1.0) for c in (True, False)}
    oni = {v: build_overnight_series(daily, mday, vix, v) for v in (14.0, 17.0, 20.0, 22.0)}

    start, end = daily.index[0] + pd.Timedelta(days=30), daily.index[-1]
    print(f"\n=== walk-forward GAP (direction+threshold chosen on train only) ===")
    gap_oos, gap_ch = wf(gap, start, end, "gap")
    print(f"\n=== walk-forward OVERNIGHT (vix threshold chosen on train only) ===")
    oni_oos, oni_ch = wf(oni, start, end, "oni")

    print("\n=========== STITCHED OOS ===========")
    sg = stats(gap_oos, "gap-CONT wf OOS ")
    so = stats(oni_oos, "overnight wf OOS  ")
    combo = gap_oos.add(oni_oos, fill_value=0.0).sort_index()
    sc = stats(combo, "PORTFOLIO (sum)   ")
    if not gap_oos.empty and not oni_oos.empty:
        joined = pd.concat([gap_oos, oni_oos], axis=1, join="inner")
        corr = float(joined.corr().iloc[0, 1]) if len(joined) > 2 else float("nan")
        print(f"overlap days: {len(joined)} | daily-return correlation: {corr:.2f}")
    print("\nportfolio yearly:")
    if not combo.empty:
        yr = combo.groupby(combo.index.year).agg(["count", "sum"])
        for y, r in yr.iterrows():
            print(f"    {y}: n={int(r['count'])}, {r['sum']*100:+.2f}%")

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"gap": gap_oos, "overnight": oni_oos, "portfolio": combo}).fillna(0.0).to_csv(
        RESULTS / "gap_overnight_oos_daily.csv")
    pd.DataFrame({"gap_choices": gap_ch, "oni_choices": oni_ch}).to_json(
        RESULTS / "gap_overnight_wf_choices.json", orient="index")
    print(f"\nsaved -> results/gap_overnight_oos_daily.csv, results/gap_overnight_wf_choices.json")


if __name__ == "__main__":
    main()
