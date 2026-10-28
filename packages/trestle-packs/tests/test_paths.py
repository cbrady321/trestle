"""Path helper tests."""

from __future__ import annotations

from pathlib import Path

from trestle_packs.core.paths import resolve_under


def test_resolve_under_relative() -> None:
    base = Path("/project")
    assert resolve_under(base, "tests") == Path("/project/tests")


def test_resolve_under_absolute_unchanged() -> None:
    base = Path("/project")
    absolute = Path("/elsewhere/tests")
    assert resolve_under(base, absolute) == absolute
