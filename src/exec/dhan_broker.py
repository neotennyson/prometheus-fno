from __future__ import annotations

import os

from src.data.dhan_client import DhanClient
from src.exec.broker_base import BrokerBase


class DhanBroker(BrokerBase):
    def __init__(self, client: DhanClient, algo_id: str = "", dry_run: bool = True):
        self.c = client
        self.algo_id = algo_id or os.environ.get("DHAN_ALGO_ID", "")
        self.dry_run = dry_run
        self._ltp_cache: dict[tuple, float] = {}

    def ltp(self, seg: str, sid: str) -> float:
        r = self.c.ltp([(seg, sid)])
        data = r.get("data", {})
        section = data.get(seg, {})
        entry = section.get(sid, {})
        return float(entry.get("last_price", 0.0))

    def market_order(self, seg: str, sid: str, qty: int, side: str, tag: str = "") -> dict:
        body = {
            "transactionType": side.upper(),
            "exchangeSegment": seg,
            "productType": "MIS",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": sid,
            "quantity": int(qty),
            "disclosedQuantity": 0,
            "price": 0,
            "afterMarketOrder": False,
            "amoTime": "OPEN",
            "boProfitValue": 0.0,
            "boStopLossValue": 0.0,
        }
        if not self.dry_run:
            if not self.algo_id:
                raise RuntimeError("live orders blocked: DHAN_ALGO_ID not set (SEBI algo framework)")
            resp = self.c.place_order(body)
            return {"orderId": resp.get("orderId"), "status": resp.get("orderStatus")}
        return {"orderId": None, "dry_run": True}

    def positions(self) -> dict:
        if self.dry_run:
            return {}
        return self.c._get("/positions")

    @staticmethod
    def option_sids(strike: int, kind: str, base_id: str = "49081") -> tuple[str, str]:
        suffix = "CE" if kind.upper() in ("CE", "C") else "PE"
        year2 = "26"
        sid = f"{base_id}{year2}{strike}{suffix}"
        return sid, suffix
