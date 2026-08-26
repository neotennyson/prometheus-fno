"""ATLAS refinement pass 2:
  R1) nr7_bracket_v2 -- close-confirmed entry (day must CLOSE beyond the coil band)
      with an ATR chandelier trail instead of reverse-band/EOD. Cross-validated on
      NIFTY *and* BankNifty (instrument robustness gate).
  R2) tod_window_scan -- exhaustive time-of-day window scan on the 1-min panel.
      IS (pre-2024) requires |t| >= T_MIN; survivors must keep sign & |t| >= 1.5 OOS.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.atlas.data_global import load_daily, repo_root
from src.backtest.intraday_engine import make_trade
from src.config import Settings
from src.costs import CostModel
from src.data.intraday import load_spot_1min, split_by_day

T_MIN_IS = 3.0


# --------------------------------------------------------------- R1: nr7 v2
def generate_nr7_close_confirm_trades(daily: pd.DataFrame, params: dict,
                                      lot: int, fut_cost_fn, equity_fn,
                                      risk_cfg: dict) -> list[dict]:
    from src.atlas import features as F
    buf_atr = float(params.get("buf_atr", 0.10))
    pctile_max = float(params.get("range_pctile_max", 0.50))
    trail_mult = float(params.get("trail_atr", 1.0))
    atr_s = F.atr(daily, 14)
    coil = (F.nr7_flag(daily, 7) | F.inside_bar_flag(daily)) & \
           (F.range_pctile(daily, 60) <= pctile_max)

    trades = []
    i_arr = np.flatnonzero(coil.to_numpy())
    for i in i_arr:
        j = i + 1
        if j + 1 >= len(daily):
            continue
        hi_prev, lo_prev = float(daily["high"].iloc[i]), float(daily["low"].iloc[i])
        a_j = float(atr_s.iloc[j])
        if not np.isfinite(a_j):
            continue
        c_j = float(daily["close"].iloc[j])
        up_lvl, dn_lvl = hi_prev + buf_atr * a_j, lo_prev - buf_atr * a_j
        # close confirmation: session j must CLOSE beyond the band
        if c_j > up_lvl:
            dir_, trigger = 1, up_lvl
        elif c_j < dn_lvl:
            dir_, trigger = -1, dn_lvl
        else:
            continue
        k = j + 1                                          # enter next session open
        entry = float(daily["open"].iloc[k])
        eq = equity_fn(daily.index[k])
        lots = int(risk_cfg.get("fixed_lots") or 1)
        trail = entry - dir_ * trail_mult * a_j
        best = entry
        exit_px, reason, exit_k = None, None, None
        for m in range(k, len(daily)):
            c_m = float(daily["close"].iloc[m])
            best = max(best, c_m) if dir_ > 0 else min(best, c_m)
            trail = (best - trail_mult * float(atr_s.iloc[m])) if dir_ > 0 else \
                    (best + trail_mult * float(atr_s.iloc[m]))
            breached = c_m <= trail if dir_ > 0 else c_m >= trail
            if breached:
                px = min(c_m, trail) if dir_ > 0 else max(c_m, trail)
                exit_px, reason, exit_k = px, "trail", m
                break
        if exit_px is None:
            exit_k = len(daily) - 1
            exit_px, reason = float(daily["close"].iloc[-1]), "end"
        tr = make_trade(daily.index[k].date(), dir_, entry, exit_px, reason,
                        lots, lot, fut_cost_fn)
        if tr:
            trades.append(tr)
    return trades


# ------------------------------------------------------------ R2: TOD scan
def scan_tod_windows(min1: dict, cost_pts: float,
                     win_minutes=(30, 60, 90, 120)) -> pd.DataFrame:
    """Per-session cumulative log returns between every (start,end) minute pair on
    a 30-min grid. Returns IS/OOS stats per window sorted by |IS t|."""
    rows = {}
    for d, g in min1.items():
        c = g["close"].to_numpy(dtype=float)
        mins = (g.index.hour * 60 + g.index.minute).to_numpy()
        lr = np.diff(np.log(np.maximum(c, 1e-9)), prepend=np.log(c[0]))
        for start in range(9 * 60 + 15, 15 * 60):
            s_idx = int(np.searchsorted(mins, start))
            if s_idx >= len(c):
                continue
            for wlen in win_minutes:
                end = start + wlen
                e_idx = int(np.searchsorted(mins, min(end, 15 * 60 + 29)))
                if e_idx <= s_idx:
                    continue
                rows.setdefault((start, wlen), {})[d] = float(lr[s_idx:e_idx].sum())
    recs = []
    cut = pd.Timestamp("2024-01-01")
    for (start, wlen), by_day in rows.items():
        s = pd.Series({pd.Timestamp(k): v for k, v in by_day.items()}).sort_index()
        is_r = s[s.index < cut]
        oos_r = s[s.index >= cut]
        if len(is_r) < 400 or len(oos_r) < 120:
            continue
        m_is, sd_is = float(is_r.mean()), float(is_r.std(ddof=0))
        if sd_is == 0:
            continue
        t_is = m_is / sd_is * np.sqrt(len(is_r))
        m_oos, sd_oos = float(oos_r.mean()), float(oos_r.std(ddof=0))
        t_oos = m_oos / sd_oos * np.sqrt(len(oos_r)) if sd_oos > 0 else 0.0
        ann_bp = m_oos * 250 / 1e-4                       # bp per year (1x exposure)
        cost_bp_yr = cost_pts * 250 / 100.0               # RT pts -> bp/yr approx
        recs.append({"start": f"{start//60:02d}:{start%60:02d}",
                     "minutes": wlen, "t_is": round(t_is, 2),
                     "t_oos": round(t_oos, 2),
                     "oos_bp_per_year": round(ann_bp, 1),
                     "cost_drag_bp_per_year": round(cost_bp_yr, 1),
                     "net_bp_per_year": round(ann_bp - cost_bp_yr, 1)})
    df = pd.DataFrame(recs)
    return df.reindex(df["t_is"].abs().sort_values(ascending=False).index)


def _ts(d):
    return pd.Timestamp(d)


def main():
    cfg = Settings.load().raw
    cm = CostModel.from_cfg(cfg["costs"])
    lot = int(cfg["universe"]["lot_size"])

    print("[atlas2] R1: nr7 close-confirm on NIFTY + BankNifty ...")
    out_rows = []
    risk = {"fixed_lots": 1}
    for inst in ("nifty", "banknifty"):
        daily = load_daily(inst)
        for buf in (0.05, 0.10, 0.20):
            for trail in (0.75, 1.25):
                trs = generate_nr7_close_confirm_trades(
                    daily, {"buf_atr": buf, "range_pctile_max": 0.50,
                            "trail_atr": trail}, lot, cm.futures_round_trip,
                    lambda ts: 500000.0, risk)
                pnl = pd.Series({pd.Timestamp(t["date"]): t["pnl"] for t in trs})
                pnl = pnl.groupby(level=0).sum().sort_index() if len(pnl) else pnl
                oos = pnl[pnl.index >= pd.Timestamp("2024-01-01")]
                def _st(x):
                    if len(x) == 0:
                        return {}
                    r = x / 500000.0
                    sd = float(r.std(ddof=0))
                    sh = float(r.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
                    eqc = 500000.0 + x.cumsum()
                    dd = float(((eqc - eqc.cummax()) / eqc.cummax()).min())
                    wins, loss = x[x > 0], x[x < 0]
                    pf = float(wins.sum() / abs(loss.sum())) if loss.sum() else float("inf")
                    return {"n": len(x), "sharpe": round(sh, 2), "maxdd": round(dd * 100, 2),
                            "pf": round(pf, 2), "total": round(float(x.sum()), 0)}
                row = {"inst": inst, "buf": buf, "trail": trail,
                       "full": _st(pnl), "is": _st(pnl[pnl.index < pd.Timestamp('2024-01-01')]),
                       "oos": _st(oos)}
                out_rows.append(row)
                print(f"  {inst} buf={buf} trail={trail} "
                      f"full={row['full']} oos={row['oos']}")

    print("[atlas2] R2: time-of-day window scan ...")
    min1 = split_by_day(load_spot_1min())
    rt_cost_pts = cm.futures_round_trip(24000.0, 1, lot)["total"] / lot
    scan = scan_tod_windows(min1, rt_cost_pts)
    sig = scan[scan["t_is"].abs() >= T_MIN_IS]
    print(f"  windows scanned: {len(scan)} | IS-significant (|t|>={T_MIN_IS}): {len(sig)}")
    validated = sig[((sig['t_is'] > 0) == (sig['t_oos'] > 0)) & (sig['t_oos'].abs() >= 1.5)] \
        if len(sig) else sig
    print(validated.head(12).to_string(index=False))

    out = repo_root() / "results" / "atlas"
    out.mkdir(parents=True, exist_ok=True)
    (out / "refine2_nr7.json").write_text(json.dumps(out_rows, indent=2, default=str))
    scan.to_csv(out / "tod_scan.csv", index=False)
    print(f"[atlas2] saved -> {out}")


if __name__ == "__main__":
    main()
