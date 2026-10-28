"""G-B1 (BFD-12), flipped by L.CL-C1.2: with two marked callables, `describe` and execution
used to pick different functions. The plugin is now refused at publication (WR-PLAN-4: the
callable that runs is the one that was described, or publication refuses it)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.proof import harness
from tests.proof.markers import target_check
from trestle.child.main import _load_plugin_callable
from trestle.common.types import RequestOutcome
from trestle.server.plugin_schema import EntryError, find_trestle_function

PLUGIN = Path(__file__).parent / "plugins" / "two_marked.py"


def _described_name() -> str | None:
    try:
        fn = find_trestle_function(ast.parse(PLUGIN.read_text(encoding="utf-8")))
    except EntryError:
        return None
    assert fn is not None
    return fn.name


def _executed_name(monkeypatch: pytest.MonkeyPatch) -> str | None:
    # The child loader registers its module under a fixed name; restore it.
    monkeypatch.setitem(sys.modules, "trestle_plugin_script", None)
    try:
        return _load_plugin_callable(PLUGIN).__name__
    except RuntimeError:
        return None


@pytest.mark.proves("WR-PLAN-4", "WR-PLAN-4:described-callable-runs", "core", "core", "must", "CI")
def test_target_described_is_executed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kernel = harness.fresh_kernel([tmp_path / "plugins"], home=tmp_path / "home")
    published = kernel.control.publish_plugin(PLUGIN.read_text(encoding="utf-8"))
    if isinstance(published, RequestOutcome):
        # refused at publication: nothing can be described and then run as another function
        assert published.origin == "publication"
        assert kernel.registry.get("zeta") is None and kernel.registry.get("alpha") is None
        return
    described = _described_name()
    executed = _executed_name(monkeypatch)
    target_check(
        described == executed,
        "G-B1",
        f"describe names {described!r} but the child executes {executed!r}",
    )
