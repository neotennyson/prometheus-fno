from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.models.black_scholes import bs_price


@dataclass
class IronFlyPlan:
    atm: float
    wing_width: int
    lot: int
    credit_pts: float
    short_ce_iv: float
    short_pe_iv: float


def round_strike(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


def years_remaining(minutes_to_settle):
    import numpy as np
    return np.maximum(minutes_to_settle, 1.0) / (252.0 * 375.0)


def plan_iron_fly(spot: float, vix_prev: float, lot: int, strike_step: int,
                  wing_width: int, iv_mult: float) -> tuple[IronFlyPlan, dict]:
    k = round_strike(spot, strike_step)
    iv = max(vix_prev * iv_mult, 4.0) / 100.0
    t_entry = years_remaining(325.0)
    r = 0.065
    ce_short = bs_price(spot, k, t_entry, iv, r=r, kind="CE")
    pe_short = bs_price(spot, k, t_entry, iv, r=r, kind="PE")
    ce_wing = bs_price(spot, k + wing_width, t_entry, iv, r=r, kind="CE")
    pe_wing = bs_price(spot, k - wing_width, t_entry, iv, r=r, kind="PE")
    net = float(ce_short + pe_short - ce_wing - pe_wing)
    plan = IronFlyPlan(atm=float(k), wing_width=int(wing_width), lot=int(lot),
                       credit_pts=float(net),
                       short_ce_iv=float(iv), short_pe_iv=float(iv))
    legs = {
        "short_ce": {"k": k, "px": float(ce_short)},
        "short_pe": {"k": k, "px": float(pe_short)},
        "long_ce": {"k": k + wing_width, "px": float(ce_wing)},
        "long_pe": {"k": k - wing_width, "px": float(pe_wing)},
    }
    return plan, legs


def structure_value_at(spot: float, legs: dict, minutes_left: float, iv: float, r: float = 0.065) -> float:
    t = years_remaining(minutes_left)
    v = (
        bs_price(spot, legs["short_ce"]["k"], t, iv, r=r, kind="CE")
        + bs_price(spot, legs["short_pe"]["k"], t, iv, r=r, kind="PE")
        - bs_price(spot, legs["long_ce"]["k"], t, iv, r=r, kind="CE")
        - bs_price(spot, legs["long_pe"]["k"], t, iv, r=r, kind="PE")
    )
    return float(v)


def simulate_expiry_day(day_open: float, day_high: float, day_low: float, day_close: float,
                        vix_prev: float, params: dict, lot: int, strike_step: int,
                        cm_leg_cost, equity: float, risk_cfg: dict) -> dict:
    entry_frac = 0.35
    px_entry = day_open + entry_frac * (day_close - day_open)
    wing = int(params.get("wing_width", 150))
    plan, legs = plan_iron_fly(px_entry, vix_prev, lot, strike_step, wing,
                               float(params.get("iv_to_vix_mult", 1.08)))
    credit = plan.credit_pts
    min_credit = float(params.get("min_credit_pts", 55))
    if credit < min_credit or not math.isfinite(credit):
        return {"traded": False, "reason": "thin_credit", "credit": credit}

    stop_mult = float(params.get("stop_mult", 2.0))
    keep_pct = float(params.get("target_keep_pct", 0.45))
    buffer = float(params.get("buffer_pts", 30))
    r = 0.065
    iv_e = plan.short_ce_iv

    move_pct = abs(px_entry - plan.atm) / plan.atm
    iv_stress = min(iv_e * (1.0 + 0.5 * move_pct / 0.01), iv_e * 1.5)

    touched_up = day_high >= plan.atm + buffer
    touched_dn = day_low <= plan.atm - buffer
    stopped = False
    if touched_up or touched_dn:
        worst_spot = plan.atm + buffer if touched_up else plan.atm - buffer
        val_at_worst = structure_value_at(worst_spot, legs, 270.0, iv_stress)
        if val_at_worst >= credit * stop_mult:
            stopped = True

    result = {
        "traded": True,
        "atm": plan.atm,
        "credit_pts": credit,
        "stopped": False,
        "target_hit": False,
        "time_exit": False,
        "gross_pts": 0.0,
    }

    if stopped:
        exit_val = credit * stop_mult * 1.03
        gross_pts = credit - exit_val
        result.update({"stopped": True, "exit_val": exit_val, "gross_pts": gross_pts})
    else:
        target_val = credit * keep_pct
        g = np.linspace(0.005, 1.0, 120)
        spots = px_entry + g * (day_close - px_entry)
        frac_of_move = entry_frac + g * (1.0 - entry_frac)
        mins_left = np.maximum(375.0 * (1.0 - frac_of_move) - 12.0, 12.01)
        hit_idx = None
        for i in range(len(g)):
            v = structure_value_at(float(spots[i]), legs, float(mins_left[i]), iv_stress)
            if v <= target_val:
                hit_idx = i
                break
        if hit_idx is not None:
            gross_pts = credit - target_val
            result.update({"target_hit": True, "exit_val": target_val, "gross_pts": gross_pts})
        else:
            val = structure_value_at(day_close, legs, 12.0, iv_stress)
            gross_pts = credit - val
            result.update({"time_exit": True, "exit_val": val, "gross_pts": gross_pts})

    qty_lots = risk_lots(equity, risk_cfg, plan, wing)
    if qty_lots <= 0:
        return {"traded": False, "reason": "no_size", "credit": credit}

    ratio = result["exit_val"] / credit if credit > 0 else 1.0
    costs_in = _leg_costs(_leg_prices(legs), cm_leg_cost, qty_lots)
    costs_out = _leg_costs(_leg_prices(legs), cm_leg_cost, qty_lots, ratio=ratio, flip=True)
    pnl = result["gross_pts"] * lot * qty_lots - costs_in - costs_out
    result.update({"lots": qty_lots, "costs": costs_in + costs_out, "pnl": pnl})
    return result


def _leg_costs(leg_items, cm_leg_cost, qty_lots: int, ratio: float = 1.0, flip: bool = False) -> float:
    total = 0.0
    for name, px in leg_items:
        is_short = name.startswith("short")
        if flip:
            side = "buy" if is_short else "sell"
        else:
            side = "sell" if is_short else "buy"
        total += cm_leg_cost(px * ratio, side, qty_lots)["total"]
    return total


def _leg_prices(legs: dict):
    return [
        ("short_ce", legs["short_ce"]["px"]),
        ("long_ce", legs["long_ce"]["px"]),
        ("short_pe", legs["short_pe"]["px"]),
        ("long_pe", legs["long_pe"]["px"]),
    ]


def risk_lots(equity: float, risk_cfg: dict, plan: IronFlyPlan, wing: int) -> int:
    max_loss_per_lot = max((wing - plan.credit_pts), 10.0) * plan.lot
    risk_budget = equity * risk_cfg["risk_per_trade_pct"] / 100.0
    lots = int(risk_budget // max_loss_per_lot)
    cap = max(1, math.floor(risk_cfg["max_lots_per_10l"] * (equity / 1_000_000.0)))
    return max(0, min(lots, cap))
