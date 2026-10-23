"""Quoting the end of a command's output in a verdict."""

from __future__ import annotations

TAIL_CHARS = 2000


def tail(text: str, limit: int = TAIL_CHARS) -> str:
    """The last `limit` characters of `text`, stripped: what a verdict quotes of a command."""
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]
