from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        p = Path(path) if path else ROOT / "config" / "settings.yaml"
        with open(p) as f:
            cfg = yaml.safe_load(f)
        return cls(raw=cfg)

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)


def project_root() -> Path:
    return ROOT
