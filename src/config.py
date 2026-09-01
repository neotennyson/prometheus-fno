from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> dict:
    """Load KEY=VALUE pairs from project .env into os.environ (never override
    existing env). Local-only by gitignore; the one place secrets may live."""
    p = path or ROOT / ".env"
    loaded = {}
    if not p.exists():
        return loaded
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded[k] = v
    return loaded


load_dotenv()  # creds available the moment any src module is imported


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
