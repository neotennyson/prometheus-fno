from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.costs import CostModel


@dataclass
class RiskState:
    equity_start_day: float
    week_start_equity: float
    realized_today: float = 0.0
    realized_this_week: float = 0.0
    open_exposure_lots: int = 0

    @property
    def daily_loss_pct(self) -> float:
        dd = -self.realized_today / self.equity_start_day * 100.0 if self.equity_start_day else 0.0
        return max(dd, 0.0)

    @property
    def weekly_loss_pct(self) -> float:
        dd = -self.realized_this_week / self.week_start_equity * 100.0 if self.week_start_equity else 0.0
        return max(dd, 0.0)


class RiskManager:
    def __init__(self, cfg: dict, cost_model: CostModel):
        self.cfg = cfg
        self.cm = cost_model
        self.state: RiskState | None = None
        self.tripped_reason: str | None = None

    def on_new_day(self, equity: float, week_start_equity: float | None = None):
        ws = week_start_equity if week_start_equity is not None else equity
        self.state = RiskState(equity_start_day=equity, week_start_equity=ws)

    def on_realized(self, pnl: float):
        assert self.state
        self.state.realized_today += pnl
        self.state.realized_this_week += pnl

    def check_circuit_breakers(self) -> bool:
        assert self.state
        if self.state.daily_loss_pct >= self.cfg["daily_loss_kill_pct"]:
            self.tripped_reason = f"daily kill switch hit ({self.state.daily_loss_pct:.2f}%)"
            return True
        if self.state.weekly_loss_pct >= self.cfg["weekly_loss_kill_pct"]:
            self.tripped_reason = f"weekly kill switch hit ({self.state.weekly_loss_pct:.2f}%)"
            return True
        return False

    def lots_for_max_loss(self, equity: float, max_loss_per_lot: float) -> int:
        risk_budget = equity * self.cfg["risk_per_trade_pct"] / 100.0
        if max_loss_per_lot <= 0:
            return 0
        lots = int(risk_budget // max_loss_per_lot)
        cap_by_capital = max(
            1, math.floor(self.cfg["max_lots_per_10l"] * (equity / 1_000_000.0))
        )
        return max(0, min(lots, cap_by_capital))

    def lots_for_stop_risk(self, equity: float, entry_price: float, stop_price: float, lot: int) -> int:
        per_lot = abs(entry_price - stop_price) * lot
        risk_budget = equity * self.cfg.get("risk_per_trade_pct", 1.0) / 100.0
        if per_lot <= 0:
            return 0
        return max(0, int(risk_budget // per_lot))
