from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.backtest import metrics as M
from src.costs import CostModel
from src.data.calendar import all_holidays, weekly_expiries_between
from src.strategies.gap_reversion import generate_gap_trades
from src.strategies.overnight import backtest_overnight
from src.strategies.trend_hourly import generate_signals, simulate_trend
from src.strategies.iron_fly_expiry import simulate_expiry_day


class Backtester:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.capital = cfg["capital"]
        self.cm = CostModel.from_cfg(cfg["costs"])
        self.lot = cfg["universe"]["lot_size"]
        self.step = cfg["universe"]["strike_step"]
        self.risk = cfg["risk"]

    def fut_round_trip(self, price, lots, lot):
        return self.cm.futures_round_trip(price, lots, lot)

    def opt_leg_cost(self, premium, side, lots):
        return self.cm.per_option_leg_entry(premium, side, lots, self.lot)

    def run_iron_fly(self, nifty: pd.DataFrame, vix: pd.Series, params: dict,
                     start: date, end: date) -> pd.Series:
        hols = all_holidays()
        expiries = weekly_expiries_between(start, end, hols)
        idx_dates = {ts.date(): ts for ts in nifty.index}
        rows = []
        equity = self.capital
        week_key = None
        realized_week = 0.0
        daily_kill = self.risk["daily_loss_kill_pct"]
        for e in expiries:
            if e not in idx_dates:
                continue
            ts_e = idx_dates[e]
            prior_idx = nifty.index[nifty.index < ts_e]
            if len(prior_idx) == 0:
                continue
            prev_tday = prior_idx[-1]
            v = float(vix.reindex([prev_tday]).ffill().iloc[-1]) if len(vix.dropna()) else np.nan
            if not np.isfinite(v) or v > self.risk["vix_max_for_theta"]:
                continue
            wk = e.isocalendar()[:2]
            if wk != week_key:
                week_key, realized_week = wk, 0.0
            day_equity = equity
            res = simulate_expiry_day(
                day_open=float(nifty.at[ts_e, "open"]),
                day_high=float(nifty.at[ts_e, "high"]),
                day_low=float(nifty.at[ts_e, "low"]),
                day_close=float(nifty.at[ts_e, "close"]),
                vix_prev=v,
                params=params,
                lot=self.lot,
                strike_step=self.step,
                cm_leg_cost=self.opt_leg_cost,
                equity=day_equity,
                risk_cfg=self.risk,
            )
            if res.get("traded"):
                pnl = res["pnl"]
                rows.append({"date": e, "pnl": pnl, **{k: res.get(k) for k in ("atm", "credit_pts", "stopped", "target_hit", "lots")}})
                equity += pnl
                realized_week += pnl
                if realized_week < 0 and abs(realized_week) / equity * 100 >= daily_kill:
                    break
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.Series(dtype=float)
        s = df.set_index("date")["pnl"]
        s.index = pd.to_datetime(s.index)
        self.last_if_trades = df
        return s

    def run_gap(self, nifty: pd.DataFrame, vix: pd.Series, params: dict,
                start: date, end: date) -> pd.Series:
        sub = nifty.loc[str(start):str(end)]
        trades = generate_gap_trades(sub, vix, params, self.lot, self.fut_round_trip,
                                     lambda idx: self.capital, self.risk)
        if not trades:
            return pd.Series(dtype=float)
        df = pd.DataFrame(trades)
        df["date"] = pd.to_datetime(df["date"])
        self.last_gap_trades = df
        return df.groupby("date")["pnl"].sum()

    def run_trend(self, hourly: pd.DataFrame, params: dict) -> pd.Series:
        sig = generate_signals(hourly, int(params["ema_fast"]), int(params["ema_slow"]),
                               int(params["adx_period"]), float(params["adx_min"]))
        trades = simulate_trend(sig, self.lot, self.fut_round_trip,
                                lambda ts: self.capital,
                                {"risk_per_trade_pct": params.get("risk_pct", 0.5)})
        if not trades:
            return pd.Series(dtype=float)
        df = pd.DataFrame(trades)
        cost_rows = df[df.get("cost_only", False) == True]
        real = df[df.get("cost_only", False) != True].copy() if "cost_only" in df.columns else df.copy()
        entry_costs = cost_rows["pnl"].sum() if not cost_rows.empty else 0.0
        real["exit_ts"] = pd.to_datetime(real["exit_ts"])
        s = real.groupby(real["exit_ts"].dt.date)["pnl"].sum() + entry_costs / max(len(real), 1)
        s.index = pd.to_datetime(s.index)
        self.last_trend_trades = real
        return s

    def run_overnight(self, nifty: pd.DataFrame, params: dict, vix: pd.Series | None = None) -> pd.Series:
        min_eq = float(params.get("min_equity", 2_000_000))
        ref_cap = max(self.capital, min_eq)
        s, info = backtest_overnight(nifty, params, self.lot, self.fut_round_trip,
                                     ref_cap, min_eq, vix=vix)
        info["capital_gated"] = bool(self.capital < min_eq)
        self.last_on_info = info
        return s

    def combine(self, *series: pd.Series) -> pd.Series:
        frames = [s for s in series if s is not None and len(s)]
        if not frames:
            return pd.Series(dtype=float)
        df = pd.concat(frames).groupby(level=0).sum()
        return df.sort_index()
