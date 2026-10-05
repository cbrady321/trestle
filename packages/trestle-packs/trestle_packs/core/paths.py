"""Path helpers for pack plugins."""

from __future__ import annotations

from pathlib import Path


def resolve_under(base: Path, path: str | Path) -> Path:
    """Resolve ``path`` relative to ``base`` when not absolute."""
    target = Path(path)
    return target if target.is_absolute() else base / target
