from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.exec.broker_base import BrokerBase


@dataclass
class PaperPosition:
    seg: str
    sid: str
    symbol: str
    qty: int
    avg_px: float
    strategy: str
    opened_at: str


class PaperBroker(BrokerBase):
    def __init__(self, quote_fn, cost_model, db_path: str = "logs/trades.db",
                 starting_cash: float = 500_000.0, slippage_pct: float = 0.002,
                 min_slip: float = 0.75):
        self.quote_fn = quote_fn
        self.cm = cost_model
        self.cash = starting_cash
        self.slippage_pct = slippage_pct
        self.min_slip = min_slip
        self.pos_book: dict[str, PaperPosition] = {}
        self.realized_today = 0.0
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS fills (
                id TEXT PRIMARY KEY, ts TEXT, strategy TEXT, seg TEXT, sid TEXT,
                symbol TEXT, side TEXT, qty INTEGER, px REAL, slip REAL, cash_after REAL)
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                ts TEXT, kind TEXT, payload TEXT)
        """)
        self.db.commit()

    def log_event(self, kind: str, payload: dict):
        self.db.execute("INSERT INTO events VALUES (?,?,?)",
                        (datetime.now().isoformat(), kind, json.dumps(payload, default=str)))
        self.db.commit()

    def ltp(self, seg: str, sid: str) -> float:
        return float(self.quote_fn(seg, sid))

    def _fill_px(self, px: float, side: str) -> float:
        slip = max(px * self.slippage_pct, self.min_slip)
        return px + slip if side.upper() == "BUY" else max(px - slip, 0.05)

    def market_order(self, seg: str, sid: str, qty: int, side: str, tag: str = "") -> dict:
        key = f"{seg}:{sid}"
        raw = self.ltp(seg, sid)
        px = self._fill_px(raw, side)
        signed = qty if side.upper() == "BUY" else -qty

        pos = self.pos_book.get(key)
        realized = 0.0
        if pos and ((pos.qty > 0 and signed < 0) or (pos.qty < 0 and signed > 0)):
            closing = min(abs(signed), abs(pos.qty))
            realized = (px - pos.avg_px) * closing * (1 if pos.qty > 0 else -1)
            pos.qty += signed
            if pos.qty == 0:
                del self.pos_book[key]
        elif pos:
            total = pos.avg_px * abs(pos.qty) + px * qty
            pos.qty += signed
            pos.avg_px = total / abs(pos.qty)
        else:
            self.pos_book[key] = PaperPosition(seg, sid, tag or key, signed, px, tag or "unknown",
                                                datetime.now().isoformat())

        fee = self.cm.per_option_leg_entry(px, "buy" if side.upper() == "BUY" else "sell", 1, 1)["total"] \
            if "OPT" in seg else self.cm.futures_round_trip(px, 1, 1)["total"] / 2.0
        self.cash -= signed * px + fee
        self.realized_today += realized - fee

        fid = uuid.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fid, datetime.now().isoformat(), tag, seg, sid, key, side.upper(), qty, round(px, 2),
             round(px - raw if side.upper() == "BUY" else raw - px, 2), round(self.cash, 2)))
        self.db.commit()
        return {"fill_id": fid, "px": px, "raw": raw}

    def flat_position(self, seg: str, sid: str, tag: str = "") -> dict | None:
        key = f"{seg}:{sid}"
        if key not in self.positions:
            return None
        pos = self.positions[key]
        side = "SELL" if pos.qty > 0 else "BUY"
        return self.market_order(seg, sid, abs(pos.qty), side, tag=tag or pos.strategy)

    def positions(self) -> dict:
        return {k: vars(v) for k, v in self.pos_book.items()}

    def equity(self) -> float:
        mtm = 0.0
        for key, pos in self.pos_book.items():
            seg, sid = key.split(":")
            try:
                mtm += pos.qty * self.ltp(seg, sid)
            except Exception:
                mtm += pos.qty * pos.avg_px
        return self.cash + mtm
