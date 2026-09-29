"""G-B1 (BFD-12): with two marked callables, `describe` and execution pick
different functions. Pin records today's split; the target requires the
described callable to be the executed one (WR-PLAN-4)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.proof.markers import target_check
from trestle.child.main import _load_plugin_callable
from trestle.server.plugin_schema import find_trestle_function

PLUGIN = Path(__file__).parent / "plugins" / "two_marked.py"


def _described_name() -> str:
    fn = find_trestle_function(ast.parse(PLUGIN.read_text(encoding="utf-8")))
    assert fn is not None
    return fn.name


def _executed_name(monkeypatch: pytest.MonkeyPatch) -> str:
    # The child loader registers its module under a fixed name; restore it.
    monkeypatch.setitem(sys.modules, "trestle_plugin_script", None)
    return _load_plugin_callable(PLUGIN).__name__


@pytest.mark.pin("G-B1")
def test_pin_described_differs_from_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _described_name() == "zeta"  # source-first
    assert _executed_name(monkeypatch) == "alpha"  # alphabetical-first
    assert _described_name() != _executed_name(monkeypatch)


@pytest.mark.target("G-B1")
@pytest.mark.proves("WR-PLAN-4", "WR-PLAN-4:described-callable-runs", "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, reason="defect:G-B1")
def test_target_described_is_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    described = _described_name()
    executed = _executed_name(monkeypatch)
    target_check(
        described == executed,
        "G-B1",
        f"describe names {described!r} but the child executes {executed!r}",
    )
