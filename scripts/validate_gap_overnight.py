import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DL = ROOT / "data" / "dhan"
SLIP_PTS = 1.5          # per side, futures-equivalent points
FEE_BPS = 0.5           # round-trip charges+STT approx in bps of notional


def ist(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df.set_index("ts").sort_index()


def perf(returns: pd.Series, tag: str):
    r = returns.dropna()
    if r.empty:
        print(f"{tag}: NO TRADES")
        return
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    sharpe = float(r.mean() / r.std(ddof=0) * np.sqrt(252)) if r.std(ddof=0) > 0 else 0.0
    print(f"{tag}: n={len(r)} | win {100*(r>0).mean():.1f}% | avg {r.mean()*1e4:.1f}bp | "
          f"total {100*(eq.iloc[-1]-1):+.2f}% | Sharpe {sharpe:.2f} | MaxDD {100*dd:.1f}%")
    yr = r.groupby(r.index.year).agg(["count", "sum"])
    for y, row in yr.iterrows():
        print(f"    {y}: n={int(row['count'])}, sum {row['sum']*100:+.2f}%")


def gap_engine(daily: pd.DataFrame, mins: pd.DataFrame, k_atr: float, stop_atr: float | None) -> pd.Series:
    """Fade |gap| >= k_atr x ATR14: enter counter-gap at 09:20 bar, exit at 15:25 close.
    Optional stop: intraday adverse move >= stop_atr x ATR14 (checked on minute closes)."""
    tr = pd.concat([daily["high"] - daily["low"],
                    (daily["high"] - daily["close"].shift()).abs(),
                    (daily["low"] - daily["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().shift(1)
    prev_close = daily["close"].shift(1)
    mdate = pd.Series(mins.index.date, index=mins.index)
    rets = {}
    for d, row in daily.iterrows():
        a, prev = atr.get(d), prev_close.get(d)
        if pd.isna(a) or pd.isna(prev) or a <= 0:
            continue
        gap = row["open"] / prev - 1.0
        if abs(gap) < k_atr * a / prev:
            continue
        day = mins[mdate == d.date()]
        if len(day) < 30:
            continue
        entry = float(day["close"].iloc[0])
        sign = -1.0 if gap > 0 else 1.0          # fade
        path = sign * (day["close"] - entry) / entry
        exit_px = float(path.iloc[-1])
        if stop_atr is not None:
            hit = path <= -stop_atr * a / entry
            if hit.any():
                exit_px = float(path[hit.idxmax()])
        fee = FEE_BPS / 1e4 + 2 * SLIP_PTS / entry
        rets[pd.Timestamp(d)] = exit_px - fee
    return pd.Series(rets).sort_index()


def overnight_engine(daily: pd.DataFrame, mins: pd.DataFrame, vix: dict, vix_min: float,
                     entry_hm: str = "15:22", exit_hm: str = "09:25") -> pd.Series:
    """VIX>=vix_min (prev close): buy near close, sell next morning's first bars."""
    dates = sorted(daily.index)
    mdate = pd.Series(mins.index.date, index=mins.index)
    rets = {}
    for i, d in enumerate(dates[:-1]):
        v = vix.get(d)                            # keys are Timestamps
        if v is None or v < vix_min:
            continue
        nxt = dates[i + 1]
        day = mins[mdate == d.date()]
        nday = mins[mdate == nxt.date()]
        e = day.between_time(entry_hm, "15:25")
        x = nday.between_time(exit_hm, exit_hm)
        if x.empty:
            x = nday.iloc[:1]
        if e.empty or x.empty:
            continue
        buy, sell = float(e["close"].iloc[0]), float(x["close"].iloc[0])
        fee = FEE_BPS / 1e4 + 2 * SLIP_PTS / buy
        rets[pd.Timestamp(nxt)] = (sell - buy) / buy - fee
    return pd.Series(rets).sort_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-06-01")
    args = ap.parse_args()
    start = pd.Timestamp(args.start)

    daily = pd.read_parquet(DL / "nifty_index_daily.parquet")
    daily.index = pd.to_datetime(daily.index)
    daily = daily[daily.index >= start]
    mins = ist(pd.read_parquet(DL / "nifty_index_minute.parquet"))
    vix_df = pd.read_parquet(ROOT / "data" / "raw" / "indiavix_daily.parquet")["close"]
    vix = dict(zip(pd.to_datetime(vix_df.index).date, vix_df.values))
    vix = {pd.Timestamp(k): v for k, v in vix.items()}

    print(f"=== REAL-PRICE VALIDATION: gap-reversion & overnight-drift (Nifty INDEX, {args.start}->2026-08) ===")
    print(f"costs model: {FEE_BPS}bp round trip + {SLIP_PTS}pts slippage/side\n")

    print("--- GAP REVERSION (a-priori spec: 0.75 ATR, no stop) ---")
    perf(gap_engine(daily, mins, 0.75, None), "gap 0.75/none")
    print("--- robustness (NOT selected, shown only) ---")
    for k, s in ((0.5, None), (1.0, None), (0.75, 1.0)):
        perf(gap_engine(daily, mins, k, s), f"gap {k}/stop {s}")

    print("\n--- OVERNIGHT DRIFT (a-priori spec: VIX>=17) ---")
    perf(overnight_engine(daily, mins, vix, 17.0), "overnight vix17")
    print("--- robustness (NOT selected, shown only) ---")
    for v in (14.0, 20.0):
        perf(overnight_engine(daily, mins, vix, v), f"overnight vix{v}")


if __name__ == "__main__":
    main()
