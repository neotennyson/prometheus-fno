import argparse
import json
import time
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.engine import Backtester
from src.backtest.metrics import monthly_table, perf_stats, save_report
from src.config import Settings, project_root
from src.data import ingest_yf


def ensure_data(force: bool = False):
    raw = project_root() / "data" / "raw"
    have_core = (raw / "nifty_daily.parquet").exists() and (raw / "indiavix_daily.parquet").exists()
    if force or not have_core:
        print("[data] downloading daily history...")
        import pandas as pd
        from src.data.ingest_yf import _chart, _save
        for name, sym in (("nifty", "^NSEI"), ("indiavix", "^INDIAVIX")):
            df = _chart(sym, "1d", period1=int(pd.Timestamp("2014-01-01").timestamp()),
                        period2=int(pd.Timestamp("2026-12-31").timestamp()))
            _save(df[["open", "high", "low", "close"]], name, "daily", raw)
            print(f"  {name}: {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()}")
            time.sleep(8)
    if force or not (raw / "nifty_hourly.parquet").exists():
        try:
            print("[data] downloading hourly nifty (last ~700d)...")
            ingest_yf.download_hourly("nifty")
        except Exception as e:
            print(f"[warn] hourly download failed ({e}); trend strategy will be skipped")


def split_stats(pnl, capital, label):
    import pandas as pd
    if pnl is None or len(pnl) == 0:
        return {"label": label, "days": 0}
    st = perf_stats(pnl, capital)
    st["label"] = label
    return st


def slice_stats(pnl, capital, oos_ts):
    import pandas as pd
    if pnl is None or len(pnl) == 0:
        return {"full": {"label": "full", "days": 0}}
    out = {"full": split_stats(pnl, capital, "full")}
    pre = pnl[pnl.index < oos_ts]
    post = pnl[pnl.index >= oos_ts]
    if len(pre):
        out["is"] = split_stats(pre, capital, "is")
    if len(post):
        out["oos"] = split_stats(post, capital, "oos")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-02-01")
    ap.add_argument("--end", default="2026-08-22")
    ap.add_argument("--oos-from", default="2024-01-01")
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args()

    cfg = Settings.load()
    if args.capital:
        cfg.raw["capital"] = args.capital

    ensure_data(args.download)

    nifty = ingest_yf.load_daily("nifty")
    vix = ingest_yf.load_daily("indiavix")["close"]
    try:
        hourly = ingest_yf.load_hourly("nifty")
    except Exception:
        hourly = None

    bt = Backtester(cfg.raw)
    s = cfg.raw["strategies"]
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    oos = date.fromisoformat(args.oos_from)

    print("[bt] iron fly (Nifty Tuesday expiries)...")
    pnl_if_full = bt.run_iron_fly(nifty, vix, s["iron_fly_expiry"], start, end)
    print(f"      trades: {len(getattr(bt, 'last_if_trades', []))}")

    print("[bt] big-gap fader...")
    pnl_gap_full = bt.run_gap(nifty, vix, s["gap_reversion"], start, end)
    print(f"      trades: {len(getattr(bt, 'last_gap_trades', []))}")

    print("[bt] overnight drift...")
    pnl_on = bt.run_overnight(nifty, s.get("overnight_drift", {}), vix)
    info = getattr(bt, "last_on_info", {})
    print(f"      trades: {info.get('trades')} | avg {info.get('avg_gross_bp')}bp/day | "
          f"gated={info.get('capital_gated')}")

    combined_full = bt.combine(pnl_if_full, pnl_gap_full, pnl_on)

    report = {
        "config": {"start": str(start), "end": str(end), "capital": cfg.raw["capital"],
                   "lot_size": bt.lot, "oos_from": str(oos)},
        "iron_fly": slice_stats(pnl_if_full, cfg.raw["capital"], pd_ts(oos)),
        "gap": slice_stats(pnl_gap_full, cfg.raw["capital"], pd_ts(oos)),
        "overnight": {"info": info, **slice_stats(pnl_on, cfg.raw["capital"], pd_ts(oos))},
        "combined": slice_stats(combined_full, cfg.raw["capital"], pd_ts(oos)),
        "monthly_combined": monthly_table(combined_full, cfg.raw["capital"]).to_dict(),
    }

    out_dir = project_root() / "results"
    out_dir.mkdir(exist_ok=True)
    save_report(report, str(out_dir / "backtest_report.json"))

    if len(combined_full):
        import pandas as pd
        eng = pd.DataFrame({
            "date": pd.to_datetime(combined_full.index),
            "combined": combined_full.values,
            "iron_fly": combined_full.index.map(lambda d: float(pnl_if_full.get(d, 0.0))),
            "gap": combined_full.index.map(lambda d: float(pnl_gap_full.get(d, 0.0))),
            "overnight": combined_full.index.map(
                lambda d: float(pnl_on.get(d, 0.0)) if pnl_on is not None else 0.0),
        })
        eng.to_csv(out_dir / "daily_pnl.csv", index=False)

    plot_equity(combined_full, cfg.raw["capital"], out_dir / "equity_curve.png")

    print("\n================ RESULTS (net of full cost stack incl. Apr-2026 STT) ================")
    for name in ("iron_fly", "gap", "overnight", "combined"):
        if name not in report:
            continue
        blk = report[name]
        for period in ("full", "is", "oos"):
            if period in blk and blk[period].get("days"):
                st = blk[period]
                print(f"{name:>10} [{st['label']:>4}] CAGR {st['cagr_pct']:>7.2f}%  Sharpe {st['sharpe']:>5.2f}  "
                      f"MaxDD {st['max_dd_pct']:>6.2f}%  PF {st['profit_factor']:>5.2f}  Win% {st['win_rate_pct']:>5.1f}  PnL ₹{st['total_pnl']:,.0f}")
    print("\nReport -> results/backtest_report.json | Curve -> results/equity_curve.png")


def pd_ts(d):
    import pandas as pd
    return pd.Timestamp(d)


def plot_equity(pnl, capital, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if pnl is None or len(pnl) == 0:
        return
    eq = capital + pnl.cumsum()
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(eq.index, eq.values, lw=1.2)
    ax.set_title("FO-BOT combined equity (₹)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)


if __name__ == "__main__":
    main()
