from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class StrategyBase(ABC):
    name: str = "base"
    params: dict = field(default_factory=dict)

    @abstractmethod
    def describe(self) -> str: ...
