"""L.CL-C1.2: one entry-selection rule; async and ambiguous entries refused (WR-PLAN-4)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.proof import harness, tolerances
from trestle.child.main import _load_plugin_callable
from trestle.common import codes
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.plugin_validate import validate_plugin

HELPER = """\
from trestle.plugin.surface import Context, trestle


@trestle
def alpha(ctx: Context) -> dict[str, str]:
    return {"ran": "alpha"}
"""

# `zeta` sorts after the imported `alpha` in dir() order and is the one defined here.
IMPORTING = """\
from entry_helper_mod import alpha
from trestle.plugin.surface import Context, trestle


@trestle
def zeta(ctx: Context) -> dict[str, str]:
    return {"ran": "zeta"}
"""

ASYNC_ENTRY = """\
from trestle.plugin.surface import Context, trestle


@trestle
async def waits(ctx: Context) -> dict[str, str]:
    return {"ran": "waits"}
"""

TWO_ENTRIES = """\
from trestle.plugin.surface import Context, trestle


@trestle
def first(ctx: Context) -> dict[str, str]:
    return {"ran": "first"}


@trestle(deadline=10)
def second(ctx: Context) -> dict[str, str]:
    return {"ran": "second"}
"""

# One definition the AST reads, a second made at import time: only the validator sees it.
HIDDEN_SECOND = """\
from trestle.plugin.surface import Context, trestle


@trestle
def visible(ctx: Context) -> dict[str, str]:
    return {"ran": "visible"}


def _make() -> None:
    @trestle
    def hidden(ctx: Context) -> dict[str, str]:
        return {"ran": "hidden"}

    globals()["hidden"] = hidden


_make()
"""


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    return harness.fresh_kernel([plugin_dir], home=tmp_path / "home")


@pytest.mark.proves("WR-PLAN-4", "WR-PLAN-4:described-callable-runs", "core", "core", "must", "CI")
def test_imported_marked_callable_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "entry_helper_mod.py").write_text(HELPER, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(lib) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    kernel = _kernel(tmp_path)

    published = kernel.control.publish_plugin(IMPORTING)
    assert isinstance(published, PublishView), published
    snap = kernel.registry.get("zeta")
    assert snap is not None
    manifest = json.loads(Path(snap.source_path).with_name("manifest.json").read_text("utf-8"))
    assert manifest["entry"] == "zeta"
    assert kernel.registry.get("alpha") is None  # the imported marker is not a plugin

    view = kernel.control.run(plugin="zeta", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView), view
    assert view.state == "succeeded"
    assert view.summary == {"ran": "zeta"}

    # the child loader reads the manifest's entry by name, in this process too
    monkeypatch.setitem(sys.modules, "trestle_plugin_script", None)
    monkeypatch.syspath_prepend(str(lib))
    assert _load_plugin_callable(Path(snap.source_path)).__name__ == "zeta"


def test_snapshot_without_manifest_entry_runs_its_one_defined_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "entry_helper_mod.py").write_text(HELPER, encoding="utf-8")
    monkeypatch.syspath_prepend(str(lib))
    plugin = tmp_path / "plugin.py"
    plugin.write_text(IMPORTING, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "trestle_plugin_script", None)
    assert _load_plugin_callable(plugin).__name__ == "zeta"


def _refused(kernel, source: str) -> RequestOutcome:  # type: ignore[no-untyped-def]
    result = kernel.control.publish_plugin(source)
    assert isinstance(result, RequestOutcome), result
    return result


@pytest.mark.proves(
    "WR-PLAN-4", "WR-PLAN-4:async-refused-at-publication", "core", "core", "must", "CI"
)
@pytest.mark.parametrize(
    ("source", "plugin", "reason"),
    [
        (ASYNC_ENTRY, "waits", "async"),
        (TWO_ENTRIES, "first", "ambiguous"),
        (HIDDEN_SECOND, "visible", "validation failed"),
    ],
    ids=["async", "ambiguous", "second-made-at-import"],
)
def test_async_and_ambiguous_refused_at_publication(
    tmp_path: Path, source: str, plugin: str, reason: str
) -> None:
    kernel = _kernel(tmp_path)
    refusal = _refused(kernel, source)
    assert refusal.origin == "publication"
    assert refusal.code == codes.PUBLICATION_VALIDATION_FAILED
    assert reason in refusal.message
    assert kernel.registry.get(plugin) is None
    if reason != "validation failed":
        # refused before anything is written; the runtime-only case is refused by the throwaway
        # validator after the source is written, as any validator refusal is
        assert not (tmp_path / "plugins" / f"{plugin}.py").exists()
    snapshots = tmp_path / "home" / "snapshots"
    assert not snapshots.exists() or not list(snapshots.iterdir())


@pytest.mark.parametrize("source", [ASYNC_ENTRY, TWO_ENTRIES], ids=["async", "ambiguous"])
def test_refused_source_dropped_in_plugin_dir_is_not_served(tmp_path: Path, source: str) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "dropped.py").write_text(source, encoding="utf-8")
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    assert kernel.registry.snapshots == {}
    snapshots = tmp_path / "home" / "snapshots"
    assert not snapshots.exists() or not list(snapshots.iterdir())


def test_validator_holds_the_entry_to_the_published_name(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        "from trestle.plugin.surface import Context, trestle\n\n\n@trestle\n"
        "def only(ctx: Context) -> dict[str, str]:\n    return {}\n",
        encoding="utf-8",
    )
    assert validate_plugin(plugin, entry="only") is None
    assert validate_plugin(plugin) is None
    error = validate_plugin(plugin, entry="other")
    assert error is not None and "'other'" in error
