"""Alembic migration runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AlembicResult:
    target: str
    config_path: str

    def to_dict(self) -> dict[str, Any]:
        return {"backend": "alembic", "target": self.target, "config": self.config_path}


def run_alembic_upgrade(config_path: str | Path, target: str = "head") -> AlembicResult:
    from alembic import command
    from alembic.config import Config

    path = Path(config_path)
    if not path.exists():
        msg = f"alembic config not found: {path}"
        raise FileNotFoundError(msg)

    cfg = Config(str(path))
    command.upgrade(cfg, target)
    return AlembicResult(target=target, config_path=str(path.resolve()))
