"""Decode the 2026 regime break.

Part 1 -- forensics: half-yearly profiles of overnight vs intraday returns,
momentum/reversion health, gap behaviour, vol state. Find WHEN and WHAT flipped.
Part 2 -- remedy test: a trailing-health regime gate (uses only past data) applied
to each surviving engine's stitched walk-forward OOS series.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from scripts.validate_gap_overnight import DL

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def halve(ts) -> str:
    return f"{ts.year}H{(ts.month - 1) // 6 + 1}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    args = ap.parse_args()

    daily = pd.read_parquet(DL / "nifty_index_daily.parquet")
    daily.index = pd.to_datetime(daily.index)
    daily = daily[daily.index >= args.start].copy()
    pc = daily["close"].shift(1)
    daily["ret"] = daily["close"] / pc - 1
    daily["on"] = daily["open"] / pc - 1          # overnight: prev close -> open
    daily["id"] = daily["close"] / daily["open"] - 1  # intraday: open -> close
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - pc).abs(),
                    (daily["low"] - pc).abs()], axis=1).max(axis=1)
    daily["atr"] = tr.rolling(14).mean()
    daily["half"] = [halve(d) for d in daily.index]

    # --- Part 1: forensic table -------------------------------------------
    print("=== FORENSICS BY HALF-YEAR ===")
    rows = []
    for h, g in daily.groupby("half"):
        big_gap = g[(g["open"] / g["close"].shift(1) - 1).abs() > 0.0075]
        ac1 = float(g["ret"].autocorr(1)) if len(g) > 20 else np.nan
        rows.append({
            "half": h,
            "vol_%": round(100 * g["ret"].std(ddof=0) * np.sqrt(252), 1),
            "on_bp/day": round(g["on"].mean() * 1e4, 1),
            "id_bp/day": round(g["id"].mean() * 1e4, 1),
            "upday_%": round(100 * (g["ret"] > 0).mean(), 0),
            "AC1": round(ac1, 2),
            "ngaps_0.75%": len(big_gap),
        })
    tb = pd.DataFrame(rows).set_index("half")
    print(tb.to_string())
    print("\nreading: 'on_bp/day' = the overnight drift engine's raw fuel;")
    print("'AC1' < 0 => market mean-reverts day-to-day (kills momentum edges).\n")

    # gap-continuation health per half (entry first bar, hold to close, k=0.75% abs gap)
    mins = pd.read_parquet(DL / "nifty_index_minute.parquet")
    mins["ts"] = pd.to_datetime(mins["ts"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    mins = mins.set_index("ts").sort_index()
    mdate = pd.Series(mins.index.date, index=mins.index)
    mfirst = mins.groupby(mdate)["close"].first()
    mlast = mins.groupby(mdate)["close"].last()
    gap_health = []
    prev_open, prev_close_prevday = None, None
    for d, row in daily.iterrows():
        prev = pc.get(d)
        if pd.isna(prev):
            continue
        gap = row["open"] / prev - 1
        ef, xl = mfirst.get(d.date()), mlast.get(d.date())
        if pd.notna(gap) and abs(gap) > 0.0075 and pd.notna(ef) and pd.notna(xl):
            dirn = 1 if gap > 0 else -1
            gap_health.append({"half": halve(d), "bp": dirn * (xl - ef) / ef * 1e4})
    gh = pd.DataFrame(gap_health).groupby("half")["bp"].agg(["count", "mean", "sum"])
    print("--- gap-CONTINUATION health by half-year (bp/trade after 09:15-bar entry) ---")
    print(gh.round(1).to_string())

    # --- Part 2: regime-gated engines --------------------------------------
    print("\n=== REGIME GATE TEST (trailing 63d overnight-PnL health, past-only) ===")
    on_d = daily["on"]
    gate_raw = on_d.shift(1).rolling(63).sum()      # available at next morning's entry
    oos_files = {
        "gap-CONT": RESULTS / "gap_overnight_oos_daily.csv",
        "overnight-v6/TOM": RESULTS / "families_oos_daily.csv",
    }
    eng = {}
    g = pd.read_csv(oos_files["gap-CONT"], index_col=0, parse_dates=True)["gap"]
    eng["gap-CONT"] = g
    fam = pd.read_csv(oos_files["overnight-v6/TOM"], index_col=0, parse_dates=True)
    for c in ("overnight", "turn_of_month"):
        eng[c] = fam[c]

    def gate_at(dates: pd.DatetimeIndex) -> pd.Series:
        vals = {}
        for d in dates:
            dd = pd.Timestamp(d) - pd.Timedelta(days=1)
            gv = gate_raw.asof(dd)
            vals[d] = bool(pd.isna(gv) or gv > 0)
        return pd.Series(vals)

    print(f"\n{'engine':16s} {'ungated':>28s} {'gated':>28s}")
    mega_u, mega_g = None, None
    for name, s in eng.items():
        s = s[s != 0]
        ga = gate_at(s.index)
        sg = s[ga]
        def line(x):
            if x.empty:
                return "EMPTY"
            eq = (1 + x).cumprod()
            sh = x.mean() / x.std(ddof=0) * np.sqrt(252) if x.std(ddof=0) > 0 else 0
            return f"{len(x)}tr {100*(eq.iloc[-1]-1):+.1f}% Sh {sh:.2f}"
        print(f"{name:16s} {line(s):>28s} {line(sg):>28s}")
        mu = s.reindex(mega_u.index if mega_u is not None else s.index, fill_value=0) if False else None
        mega_u = s.add(mega_u, fill_value=0) if mega_u is not None else s
        mega_g = sg.add(mega_g, fill_value=0) if mega_g is not None else sg

    for tag, m in (("MEGA ungated", mega_u), ("MEGA gated  ", mega_g)):
        eq = (1 + m).cumprod()
        sh = m.mean() / m.std(ddof=0) * np.sqrt(252) if m.std(ddof=0) > 0 else 0
        yr = m.groupby(m.index.year).agg(["count", "sum"])
        seg = " ".join(f"{y}:{r['sum']*100:+.1f}%" for y, r in yr.iterrows())
        print(f"{tag}: {len(m)}tr {100*(eq.iloc[-1]-1):+.2f}% Sh {sh:.2f} MaxDD {100*(eq/eq.cummax()-1).min():.1f}% | {seg}")

    tb.to_csv(RESULTS / "decode_2026_forensics.csv")
    gh.to_csv(RESULTS / "decode_2026_gaphealth.csv")
    mega_g.dropna().to_frame("gated_ret").to_csv(RESULTS / "mega_portfolio_gated.csv")
    print("\nsaved -> results/decode_2026_forensics.csv, decode_2026_gaphealth.csv, mega_portfolio_gated.csv")


if __name__ == "__main__":
    main()
