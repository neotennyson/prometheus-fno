from __future__ import annotations

import os
from collections import Counter
from datetime import date, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import Settings
from src.costs import CostModel
from src.models.black_scholes import bs_price
from src.strategies.iron_fly_expiry import years_remaining

LOT = 65
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BIG = 10 ** 9


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def dhan_dir() -> Path:
    return Path(os.environ.get("DHAN_DATA_DIR", repo_root() / "data" / "dhan"))


def load_leg(tag: str) -> pd.DataFrame | None:
    p = dhan_dir() / f"nifty_opt_{tag}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df["ts"] = df["ts"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.drop_duplicates("ts").set_index("ts").sort_index()
    df["date"] = df.index.date
    return df


def load_legs_by_wing() -> dict[int, dict]:
    base = {"atm_ce": load_leg("CALL_ATM"), "atm_pe": load_leg("PUT_ATM")}
    if any(v is None for v in base.values()):
        return {}
    out = {}
    for wing, ce_tag, pe_tag in ((100, "CALL_ATMp2", "PUT_ATMm2"), (150, "CALL_ATMp3", "PUT_ATMm3"),
                                 (200, "CALL_ATMp4", "PUT_ATMm4"), (250, "CALL_ATMp5", "PUT_ATMm5")):
        w_ce, w_pe = load_leg(ce_tag), load_leg(pe_tag)
        if w_ce is not None and w_pe is not None:
            out[wing] = {**base, "w_ce": w_ce, "w_pe": w_pe}
    return out


def implied_iv(price: float, S: float, K: float, T: float, kind: str) -> float | None:
    if price <= 0.05 or T <= 0:
        return None
    lo, hi = 0.03, 3.0
    for _ in range(40):
        mid = (lo + hi) / 2
        p = bs_price(S, K, T, mid, kind=kind)
        if p > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def detect_expiry_days(atm_ce: pd.DataFrame, threshold: float = 15.0) -> list[date]:
    rows = []
    for d, g in atm_ce.groupby(atm_ce.index.date):
        last = g.iloc[-1]
        spot, strike, close = last.get("spot"), last.get("strike"), last["close"]
        if pd.isna(spot) or pd.isna(strike):
            continue
        rows.append({"date": d, "extrinsic": float(close) - max(float(spot) - float(strike), 0.0)})
    s = pd.DataFrame(rows).set_index("date")["extrinsic"].sort_index()
    return sorted(date(x.year, x.month, x.day) for x in s[s < threshold].index)


def expiry_weekday_histogram(expiries: list[date]) -> dict[int, list[str]]:
    hist = Counter((d.year, WEEKDAYS[d.weekday()]) for d in expiries)
    out: dict[int, list[str]] = {}
    for (y, wd), n in sorted(hist.items()):
        out.setdefault(y, []).append(f"{wd}:{n}")
    return out


def calibrate_wing_skew(legs: dict, sample_days: list[date], wing: float) -> tuple[float, float]:
    bumps_ce, bumps_pe = [], []
    for d in sample_days:
        ce_a, pe_a = legs["atm_ce"][legs["atm_ce"]["date"] == d], legs["atm_pe"][legs["atm_pe"]["date"] == d]
        ce_w, pe_w = legs["w_ce"][legs["w_ce"]["date"] == d], legs["w_pe"][legs["w_pe"]["date"] == d]
        if any(len(x) == 0 for x in (ce_a, pe_a, ce_w, pe_w)):
            continue
        idx = ce_a.index.intersection(ce_w.index).intersection(pe_a.index).intersection(pe_w.index)
        for ts in idx[::5]:
            ka, kw, kp = ce_a["strike"].get(ts), ce_w["strike"].get(ts), pe_w["strike"].get(ts)
            if pd.isna(ka) or pd.isna(kw) or pd.isna(kp):
                continue
            if abs(kw - (ka + wing)) > 0.5 or abs(kp - (ka - wing)) > 0.5:
                continue
            spot = ce_a["spot"].get(ts)
            px_atm, px_wce, px_wpe = ce_a["close"].get(ts), ce_w["close"].get(ts), pe_w["close"].get(ts)
            if pd.isna(spot) or pd.isna(px_atm) or pd.isna(px_wce) or pd.isna(px_wpe):
                continue
            mins = max((pd.Timestamp(ts).normalize() + pd.Timedelta(hours=15, minutes=30) - pd.Timestamp(ts)).total_seconds() / 60, 1.0)
            T = years_remaining(mins)
            iv_atm = implied_iv(float(px_atm), float(spot), float(ka), T, "CE")
            if iv_atm is None or not (0.02 < iv_atm < 1.5):
                continue
            ivw = implied_iv(float(px_wce), float(spot), float(kw), T, "CE")
            if ivw is not None and 0.01 < ivw < 1.5:
                bumps_ce.append(ivw - iv_atm)
            ivp = implied_iv(float(px_wpe), float(spot), float(kp), T, "PE")
            if ivp is not None and 0.01 < ivp < 1.5:
                bumps_pe.append(ivp - iv_atm)
    b_ce = float(pd.Series(bumps_ce).median()) if bumps_ce else 0.0
    b_pe = float(pd.Series(bumps_pe).median()) if bumps_pe else 0.0
    return b_ce, b_pe


def load_vix() -> tuple[dict, list]:
    vix = pd.read_parquet(repo_root() / "data" / "raw" / "indiavix_daily.parquet")["close"]
    vix.index = pd.to_datetime(vix.index).date
    by_date = dict(zip(vix.index, vix.values))
    return by_date, sorted(by_date)


def load_atr(period: int = 14) -> tuple[dict, list]:
    df = pd.read_parquet(repo_root() / "data" / "raw" / "nifty_daily.parquet")
    df.index = pd.to_datetime(df.index).date
    tr = df["high"] - df["low"]
    atr = tr.rolling(period).mean().shift(1)
    by_date = {d: float(a) for d, a in zip(atr.index, atr.values) if a == a}
    return by_date, sorted(by_date)


def vix_prev_close(by_date: dict, d: date, dates: list) -> float | None:
    import bisect
    i = bisect.bisect_left(dates, d)
    return by_date[dates[i - 1]] if i > 0 else None


def _first(mask: np.ndarray) -> int:
    idx = np.flatnonzero(mask)
    return int(idx[0]) if len(idx) else BIG


class RealEngine:
    def __init__(self, legs_by_wing: dict[int, dict], exit_t: dtime = dtime(15, 18)):
        self.legs_by_wing = legs_by_wing
        self.exit_t = exit_t
        self.cm = CostModel.from_cfg(Settings.load().raw["costs"])
        self._days: dict[tuple, dict | None] = {}
        self._paths: dict[tuple, dict | None] = {}
        self._skew: dict[int, tuple[float, float]] = {}

    def skew(self, wing: int, expiries: list[date]) -> tuple[float, float]:
        if wing not in self._skew:
            sample = expiries[::max(1, len(expiries) // 60)]
            self._skew[wing] = calibrate_wing_skew(self.legs_by_wing[wing], sample, float(wing))
        return self._skew[wing]

    def _day(self, wing: int, d: date, expiry_ts: pd.Timestamp | None = None) -> dict | None:
        key = (wing, d, expiry_ts)
        if key in self._days:
            return self._days[key]
        legs = self.legs_by_wing[wing]
        ce_a = legs["atm_ce"][legs["atm_ce"]["date"] == d]
        pe_a = legs["atm_pe"][legs["atm_pe"]["date"] == d]
        ce_w = legs["w_ce"][legs["w_ce"]["date"] == d]
        pe_w = legs["w_pe"][legs["w_pe"]["date"] == d]
        if any(len(x) == 0 for x in (ce_a, pe_a, ce_w, pe_w)):
            self._days[key] = None
            return None
        idx = ce_a.index.union(pe_a.index).union(ce_w.index).union(pe_w.index)
        panel = pd.DataFrame({
            "ce_atm": ce_a["close"].reindex(idx), "pe_atm": pe_a["close"].reindex(idx),
            "ce_w": ce_w["close"].reindex(idx), "pe_w": pe_w["close"].reindex(idx),
            "spot": ce_a["spot"].reindex(idx),
            "k_atm": ce_a["strike"].reindex(idx), "k_wce": ce_w["strike"].reindex(idx),
            "k_wpe": pe_w["strike"].reindex(idx),
        }).ffill().dropna(subset=["ce_atm", "pe_atm", "ce_w", "pe_w"]).between_time("09:20", "15:25")
        if panel.empty:
            self._days[key] = None
            return None
        # IV must be solved against the SAME expiry clock the marks use; otherwise
        # sigma absorbs the T mismatch and every revaluation is poisoned.
        day_close_ts = expiry_ts if expiry_ts is not None else (
            panel.index[0].normalize() + pd.Timedelta(hours=15, minutes=30))
        # vectorized implied-IV for every bar of the day (bisection, 40 steps)
        px = panel["ce_atm"].to_numpy(dtype=float)
        sp = panel["spot"].to_numpy(dtype=float)
        kk = panel["k_atm"].to_numpy(dtype=float)
        idx_min = np.maximum((panel.index - day_close_ts).total_seconds() / 60.0, 1.0)
        tt = years_remaining(np.abs(idx_min))
        ok = (px > 0.05) & (tt > 0) & np.isfinite(sp) & np.isfinite(px)
        lo = np.full(len(px), 0.03)
        hi = np.full(len(px), 3.0)
        for _ in range(40):
            mid = (lo + hi) / 2
            p = bs_price(sp, kk, tt, mid, kind="CE")
            take = ok & (p > px)
            hi = np.where(take, mid, hi)
            lo = np.where(take, lo, mid)
        iv_arr = (lo + hi) / 2
        iv_arr[~ok | (iv_arr < 0.02) | (iv_arr > 1.5)] = np.nan
        iv_real = pd.Series(iv_arr, index=panel.index).ffill().bfill()
        out = {"panel": panel, "iv": iv_real, "close_ts": day_close_ts}
        self._days[key] = out
        return out

    def _path(self, wing: int, d: date, entry_t: dtime,
              expiry_ts: pd.Timestamp | None = None) -> dict | None:
        key = (wing, d, entry_t)
        if key in self._paths:
            return self._paths[key]
        day = self._day(wing, d, expiry_ts=expiry_ts)
        if day is None:
            self._paths[key] = None
            return None
        panel, iv_real, _ = day["panel"], day["iv"], day["close_ts"]
        # Marks must decay to the CONTRACT's expiry, not today's close. For 0DTE
        # (expiry-day trading) they coincide; for other days callers pass expiry_ts.
        day_close_ts = expiry_ts if expiry_ts is not None else (
            panel.index[0].normalize() + pd.Timedelta(hours=15, minutes=30))
        entry_rows = panel[panel.index.time >= entry_t]
        if entry_rows.empty:
            self._paths[key] = None
            return None
        e_ts = entry_rows.index[0]
        e = entry_rows.iloc[0]
        k_atm, k_wce, k_wpe = float(e["k_atm"]), float(e["k_wce"]), float(e["k_wpe"])
        if not (abs(k_wce - (k_atm + wing)) < 0.5 and abs(k_wpe - (k_atm - wing)) < 0.5):
            self._paths[key] = None
            return None
        credit = float(e["ce_atm"] + e["pe_atm"] - e["ce_w"] - e["pe_w"])
        path = panel.loc[e_ts:]
        bump_ce, bump_pe = self.skew(wing, [d])
        ce_K = pd.Series(np.where(path["k_atm"] == k_atm, path["ce_atm"],
                           np.where(path["k_wce"] == k_atm, path["ce_w"], np.nan)), index=path.index)
        ce_Kw = pd.Series(np.where(path["k_wce"] == k_wce, path["ce_w"],
                            np.where(path["k_atm"] == k_wce, path["ce_atm"], np.nan)), index=path.index)
        pe_K = pd.Series(np.where(path["k_atm"] == k_atm, path["pe_atm"],
                           np.where(path["k_wpe"] == k_atm, path["pe_w"], np.nan)), index=path.index)
        pe_Kw = pd.Series(np.where(path["k_wpe"] == k_wpe, path["pe_w"],
                            np.where(path["k_atm"] == k_wpe, path["pe_atm"], np.nan)), index=path.index)
        n = len(path)
        vals = np.empty(n)
        minutes = np.empty(n, dtype=int)
        n_real = 0
        # numpy-ify the hot loop (iterrows/.loc proved 100x too slow at scale)
        ce_a, ce_w = ce_K.to_numpy(dtype=float), ce_Kw.to_numpy(dtype=float)
        pe_a, pe_w = pe_K.to_numpy(dtype=float), pe_Kw.to_numpy(dtype=float)
        k_a_arr = path["k_atm"].to_numpy(dtype=float)
        spot_arr = path["spot"].to_numpy(dtype=float)
        iv_arr = iv_real.reindex(path.index).to_numpy(dtype=float)
        t_arr = years_remaining(np.abs((path.index - day_close_ts).total_seconds() / 60.0))
        for i in range(n):
            ts_min = int(path.index[i].hour * 60 + path.index[i].minute)
            minutes[i] = ts_min
            iv_atm = iv_arr[i]
            if not np.isfinite(iv_atm):
                vals[i] = np.nan
                continue
            spot = spot_arr[i] if np.isfinite(spot_arr[i]) else float(k_a_arr[i])
            t_years = float(t_arr[i])
            legs_px = []
            any_bs = False
            for real_px, strike, kind, is_wing in (
                (ce_a[i], k_atm, "CE", False), (ce_w[i], k_wce, "CE", True),
                (pe_a[i], k_atm, "PE", False), (pe_w[i], k_wpe, "PE", True),
            ):
                if np.isfinite(real_px):
                    legs_px.append(float(real_px))
                    continue
                any_bs = True
                iv = max(min(iv_atm + (bump_ce if kind == "CE" else bump_pe), 1.5), 0.03) if is_wing \
                    else max(min(iv_atm, 1.5), 0.03)
                legs_px.append(float(bs_price(spot, float(strike), t_years, iv, kind=kind)))
            n_real += 0 if any_bs else 1
            vals[i] = legs_px[0] + legs_px[2] - legs_px[1] - legs_px[3]
        # fill any bars skipped for missing-IV so exits never read NaN
        vals = pd.Series(vals).ffill().bfill().to_numpy()
        out = {"credit": credit, "vals": vals, "minutes": minutes, "n": n, "n_real": n_real,
               "iv_entry": float(iv_real.loc[e_ts]) if pd.notna(iv_real.loc[e_ts]) else None,
               "spot_entry": float(e["spot"]) if pd.notna(e["spot"]) else None,
               "entry_legs": [("short", float(e["ce_atm"])), ("short", float(e["pe_atm"])),
                              ("long", float(e["ce_w"])), ("long", float(e["pe_w"]))]}
        self._paths[key] = out
        return out

    def day_context(self) -> pd.DataFrame:
        if hasattr(self, "_day_ctx"):
            return self._day_ctx
        spot = self.legs_by_wing[next(iter(self.legs_by_wing))]["atm_ce"][["spot", "date"]].dropna()
        g = spot.groupby("date")["spot"]
        df = pd.DataFrame({"open": g.first(), "high": g.max(), "low": g.min(), "close": g.last()})
        prev_close = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"],
                        (df["high"] - prev_close).abs(),
                        (df["low"] - prev_close).abs()], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
        df.index = pd.to_datetime(df.index).date
        self._day_ctx = df
        return df

    def entry_ivs(self, wing: int, entry_t: dtime, expiries: list[date]) -> dict:
        out = {}
        for d in expiries:
            p = self._path(wing, d, entry_t)
            if p and p["iv_entry"]:
                out[d] = p["iv_entry"]
        ratios = {}
        hist: list[float] = []
        for d in expiries:
            iv = out.get(d)
            if iv and len(hist) >= 6:
                ratios[d] = iv / (sum(hist[-12:]) / len(hist[-12:]))
            if iv:
                hist.append(iv)
        return ratios

    def replay(self, expiries: list[date], wing: int, entry_t: dtime, keep_pct: float,
               stop_mult: float | None, min_credit: float, max_vix: float,
               vix_by_date: dict, vix_dates: list,
               size: bool = False, capital: float = 500000.0, risk: dict | None = None,
               expiry_of=None, iv_mult: float | None = None, iv_lookback: int = 12,
               chop_k: float | None = None) -> pd.DataFrame:
        """expiry_of: optional callable(date) -> contract expiry Timestamp for
        non-0DTE structures; defaults to same-day close (0DTE behaviour).
        iv_mult: only enter when entry-time ATM IV >= mult x trailing mean of the
        prior `iv_lookback` expiry days' entry IVs (vol-risk-premium filter).
        chop_k: only enter when |open->entry drift| <= k x previous day's ATR14/close."""
        exit_min = self.exit_t.hour * 60 + self.exit_t.minute
        self.skew(wing, expiries)
        iv_ratios = self.entry_ivs(wing, entry_t, expiries) if iv_mult is not None else {}
        open_by_date, typical_by_date = {}, {}
        if chop_k is not None:
            ctx = self.day_context()
            typical = (ctx["atr14"].shift(1) / ctx["close"].shift(1))
            open_by_date = dict(zip(ctx.index, ctx["open"]))
            typical_by_date = {d: t for d, t in zip(ctx.index, typical.values) if t == t}
        rows = []
        equity = capital
        for d in expiries:
            vp = vix_prev_close(vix_by_date, d, vix_dates)
            if vp is None or vp > max_vix:
                continue
            p = self._path(wing, d, entry_t,
                           expiry_ts=expiry_of(d) if expiry_of else None)
            if p is None or p["credit"] < min_credit or p["credit"] <= 0:
                continue
            if iv_mult is not None:
                r = iv_ratios.get(d)
                if r is None or r < iv_mult:
                    continue
            if chop_k is not None and p["spot_entry"]:
                o = open_by_date.get(d)
                tp = typical_by_date.get(d)
                if o and o == o and tp:
                    if abs(p["spot_entry"] / o - 1.0) > chop_k * tp:
                        continue
            credit = p["credit"]
            if size and risk is not None:
                mpl = max(float(wing) - credit, 10.0) * LOT
                lots = min(int((equity * float(risk["risk_per_trade_pct"]) / 100.0) // mpl),
                           max(1, int(float(risk["max_lots_per_10l"]) * equity / 1_000_000)))
                if lots <= 0:
                    continue
            else:
                lots = 1
            vals, minutes = p["vals"], p["minutes"]
            stop_level = credit * stop_mult if stop_mult else np.inf
            target_level = credit * keep_pct
            i_stop = _first(vals >= stop_level)
            i_tgt = _first(vals <= target_level)
            i_time = _first(minutes >= exit_min)
            i = min(i_stop, i_tgt, i_time)
            if i >= BIG:
                i, reason = len(vals) - 1, "eod"
            elif i == i_stop:
                reason = "stop"
            elif i == i_tgt:
                reason = "target"
            else:
                reason = "time"
            exit_px = float(vals[i])
            gross_pts = credit - exit_px
            ratio = max(exit_px / credit, 0.0)
            costs_in = sum(self.cm.per_option_leg_entry(px, "sell" if s == "short" else "buy", lots, LOT)["total"]
                           for s, px in p["entry_legs"])
            costs_out = sum(self.cm.per_option_leg_entry(px * ratio, "buy" if s == "short" else "sell", lots, LOT)["total"]
                            for s, px in p["entry_legs"])
            pnl = gross_pts * LOT * lots - costs_in - costs_out
            rows.append({"date": pd.Timestamp(d), "pnl": pnl, "reason": reason, "credit": credit, "lots": lots})
            if size:
                equity += pnl
        if rows:
            return pd.DataFrame(rows).set_index("date")
        return pd.DataFrame(columns=["pnl", "reason", "credit", "lots"]).set_index(pd.DatetimeIndex([]))


def daily_sharpe(trades: pd.DataFrame, start: date, end: date, capital: float = 500000.0) -> float:
    if trades.empty:
        return 0.0
    cal = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    rets = trades["pnl"].reindex(cal).fillna(0.0) / capital
    sd = float(rets.std(ddof=0))
    return float(rets.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
