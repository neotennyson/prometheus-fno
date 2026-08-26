from __future__ import annotations

from abc import ABC, abstractmethod


class BrokerBase(ABC):
    @abstractmethod
    def ltp(self, seg: str, sid: str) -> float: ...

    @abstractmethod
    def market_order(self, seg: str, sid: str, qty: int, side: str, tag: str = "") -> dict: ...

    @abstractmethod
    def positions(self) -> dict: ...
