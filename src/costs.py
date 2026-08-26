from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostModel:
    brokerage_per_order: float = 20.0
    opt_stt_sell: float = 0.0015
    opt_stt_exercise: float = 0.0015
    fut_stt_sell: float = 0.0005
    exch_opt_nse: float = 0.0003503
    exch_fut_nse: float = 0.0000173
    sebi_turnover: float = 0.000001
    stamp_opt_buy: float = 0.00003
    stamp_fut_buy: float = 0.00002
    gst_on_charges: float = 0.18
    ipft_opt: float = 0.00002
    slippage_opt_pct: float = 0.002
    slippage_fut_pts: float = 1.5
    min_slippage_per_leg: float = 0.75

    @classmethod
    def from_cfg(cls, cfg: dict) -> "CostModel":
        return cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})

    def option_round_trip(self, sell_premium: float, buy_premium: float, qty: int, lot: int) -> dict:
        sell_val = sell_premium * qty * lot
        buy_val = buy_premium * qty * lot
        stt = self.opt_stt_sell * sell_val
        exch = self.exch_opt_nse * (sell_val + buy_val)
        ipft = self.ipft_opt * (sell_val + buy_val)
        sebi = self.sebi_turnover * (sell_val + buy_val)
        stamp = self.stamp_opt_buy * buy_val
        brokerage = 2 * self.brokerage_per_order
        taxable = brokerage + exch + ipft + sebi
        gst = self.gst_on_charges * taxable
        total = stt + taxable + gst + stamp
        slip_sell = max(sell_premium * self.slippage_opt_pct * lot * qty, self.min_slippage_per_leg * lot * qty)
        slip_buy = max(buy_premium * self.slippage_opt_pct * lot * qty, self.min_slippage_per_leg * lot * qty)
        return {
            "stt": round(stt, 2),
            "exchange": round(exch, 2),
            "ipft": round(ipft, 2),
            "sebi": round(sebi, 2),
            "stamp": round(stamp, 2),
            "brokerage": round(brokerage, 2),
            "gst": round(gst, 2),
            "slippage": round(slip_sell + slip_buy, 2),
            "total": round(total + slip_sell + slip_buy, 2),
        }

    def futures_round_trip(self, price: float, qty_lots: int, lot: int) -> dict:
        val = price * qty_lots * lot
        stt = self.fut_stt_sell * val
        exch = self.exch_fut_nse * 2 * val
        sebi = self.sebi_turnover * 2 * val
        stamp = self.stamp_fut_buy * val
        brokerage = 2 * self.brokerage_per_order
        gst = self.gst_on_charges * (brokerage + exch + sebi)
        slip = self.slippage_fut_pts * qty_lots * lot * 2
        total = stt + exch + sebi + stamp + brokerage + gst + slip
        return {"total": round(total, 2)}

    def per_option_leg_entry(self, premium: float, side: str, qty_lots: int, lot: int) -> dict:
        val = premium * qty_lots * lot
        is_sell = side.lower() == "sell"
        stt = self.opt_stt_sell * val if is_sell else 0.0
        exch = self.exch_opt_nse * val
        ipft = self.ipft_opt * val
        sebi = self.sebi_turnover * val
        stamp = self.stamp_opt_buy * val if not is_sell else 0.0
        brokerage = self.brokerage_per_order
        gst = self.gst_on_charges * (brokerage + exch + ipft + sebi)
        slip = max(premium * self.slippage_opt_pct * lot * qty_lots, self.min_slippage_per_leg * lot * qty_lots)
        total = stt + exch + ipft + sebi + stamp + brokerage + gst + slip
        return {"total": round(total, 2)}
