"""L.CL-B1.1: the result summary's budget is the run's own, declared fields are reserved first, and
a declared field that cannot fit says so (WR-TERM-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.proof import harness, tolerances
from trestle.child.index import Index
from trestle.common.types import DeclaredMetadata, PublishView, RunView
from trestle.server import project as project_module
from trestle.server import projection
from trestle.server.ledger import run_dir_for

MIB = 1024 * 1024

BIG_EARLY = """\
from trestle.plugin.surface import Context, trestle


@trestle(summary_fields=("status",))
def big_early(ctx: Context, size: int = 10485760) -> dict[str, str]:
    out = {"aaa": "a" * size, "bbb": "b" * size, "status": "failed: " + "x" * 400}
    # early-sorted mid-size fields that together take the whole default budget
    out.update({f"f{n:02d}": "f" * 300 for n in range(20)})
    return out
"""

BIG_DECLARED = """\
from trestle.plugin.surface import Context, trestle


@trestle(summary_fields=("status", "verdict"))
def big_declared(ctx: Context, size: int = 10485760) -> dict[str, str]:
    return {"aaa": "a" * 100, "status": "s" * size, "verdict": "no", "zzz": "1"}
"""

FILL = """\
from trestle.plugin.surface import Context, trestle


@trestle(summary_fields={declared})
def fill_plugin(ctx: Context) -> dict[str, str]:
    return {{"aaa": "a" * 200, "bbb": "b" * 200, "ccc": "c" * 200, "ddd": "d" * 150}}
"""

FILL_BUDGET = 520  # two 200-byte fields plus the frame, not three


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    return harness.fresh_kernel([plugin_dir], home=tmp_path / "home")


def _run(kernel, plugin: str, **args: object) -> RunView:  # type: ignore[no-untyped-def]
    view = kernel.control.run(plugin=plugin, args=args, wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView), view
    assert view.state == "succeeded"
    return view


def _summary_bytes(view: RunView) -> int:
    return len(json.dumps(view.summary, separators=(",", ":")).encode("utf-8"))


@pytest.mark.proves(
    "WR-TERM-5", "WR-TERM-5:early-sorted-large-result-no-hide", "core", "core", "PROC", "CI"
)
def test_declared_status_survives_large_early_sorted_field(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(BIG_EARLY), PublishView)
    snap = kernel.registry.get("big_early")
    assert snap is not None

    view = _run(kernel, "big_early")

    assert isinstance(view.summary, dict)
    assert view.summary["status"].startswith("failed: ")
    assert _summary_bytes(view) <= snap.summary_budget
    assert view.truncated is True
    assert view.omitted is not None and {"aaa", "bbb"} <= set(view.omitted)
    assert any(name.startswith("f") for name in view.summary)  # the fill still uses the rest
    assert view.next is not None
    assert view.result_bytes is not None and view.result_bytes > 20 * MIB


@pytest.mark.proves(
    "WR-TERM-5", "WR-TERM-5:declared-cannot-fit-says-so", "core", "core", "PROC", "CI"
)
def test_declared_field_that_cannot_fit_says_so(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(BIG_DECLARED), PublishView)
    snap = kernel.registry.get("big_declared")
    assert snap is not None

    view = _run(kernel, "big_declared")

    # the 10 MiB declared field is not in the summary and is named as omitted; the declared
    # field behind it and the other fields still are.
    assert isinstance(view.summary, dict)
    assert "status" not in view.summary
    assert view.summary["verdict"] == "no"
    assert view.summary["aaa"] == "a" * 100
    assert view.truncated is True
    assert view.omitted == ["status"]
    assert view.next is not None
    markers = [m for m in view.limits_exceeded or [] if m.get("limit") == "summary_budget"]
    assert len(markers) == 1
    assert markers[0]["field"] == "status"
    assert markers[0]["reason"] == "does_not_fit"
    assert markers[0]["budget"] == snap.summary_budget
    assert markers[0]["bytes_needed"] > 10 * MIB
    # the recorded projection carries the same marker, and the handle resolves
    run_dir = run_dir_for(kernel.home, view.run_id)
    recorded = json.loads((run_dir / "evidence" / "summary.json").read_text("utf-8"))
    assert recorded["markers"] == markers
    fetched = kernel.control.fetch(view.next, {"kind": "jsonpath", "expr": "$.verdict"})
    assert isinstance(fetched, dict) and fetched["values"] == ["no"]


def test_projection_names_declared_fields_lost_to_a_coarsened_index(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_bytes(b"{}")
    coarse = Index(root_type="object", byte_length=10 * MIB, index_truncated=True, field_count=9000)

    built = projection.build_summary(
        run_id="run_x",
        result_path=result,
        index=coarse,
        budget=4096,
        declared_fields=("status", "status", "verdict"),
    )

    assert built.summary == {"field_count": 9000}
    assert built.omitted == ["*"]
    assert built.markers is not None
    assert [(m["field"], m["reason"]) for m in built.markers] == [
        ("status", "index_coarsened"),
        ("verdict", "index_coarsened"),
    ]
    undeclared = projection.build_summary(
        run_id="run_x", result_path=result, index=coarse, budget=4096
    )
    assert undeclared.markers is None


def test_declared_order_is_priority_and_reserved_before_the_fill(tmp_path: Path) -> None:
    # budget for two 200-byte fields: `ddd` (declared first) and `ccc` are reserved before the
    # fill; `aaa` and `bbb` sort first but lose to them.
    kernel = _kernel(tmp_path)
    assert isinstance(
        kernel.control.publish_plugin(FILL.format(declared='("ddd", "ccc")')), PublishView
    )
    with harness.patch_snapshot(kernel, "fill_plugin", summary_budget=FILL_BUDGET):
        view = _run(kernel, "fill_plugin")
    assert isinstance(view.summary, dict)
    assert sorted(view.summary) == ["ccc", "ddd"]
    assert view.omitted == ["aaa", "bbb"]
    assert view.limits_exceeded is None
    assert list(view.summary) == ["ccc", "ddd"]  # index order, not declared order


def test_declaring_nothing_keeps_the_sorted_greedy_fill(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(FILL.format(declared="()")), PublishView)
    with harness.patch_snapshot(kernel, "fill_plugin", summary_budget=FILL_BUDGET):
        view = _run(kernel, "fill_plugin")
    assert isinstance(view.summary, dict)
    assert sorted(view.summary) == ["aaa", "bbb"]
    assert view.omitted == ["ccc", "ddd"]
    assert view.limits_exceeded is None


@pytest.mark.proves("WR-TERM-5", "WR-TERM-5:budget-from-own-spec", "core", "core", "PROC", "CI")
def test_budget_from_run_spec_not_current_snapshot(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(FILL.format(declared='("ccc",)')), PublishView)
    with harness.patch_snapshot(kernel, "fill_plugin", summary_budget=FILL_BUDGET):
        admitted = _run(kernel, "fill_plugin")
    assert admitted.truncated is True
    spec_path = run_dir_for(kernel.home, admitted.run_id) / "evidence" / "spec.json"
    assert json.loads(spec_path.read_text("utf-8"))["summary_budget"] == FILL_BUDGET

    # a roomy budget published afterwards does not change the admitted run's projection ...
    with harness.patch_snapshot(kernel, "fill_plugin", summary_budget=64 * 1024):
        after = kernel.control.project.status(admitted.run_id)
    assert isinstance(after, RunView)
    assert after == admitted
    # ... and the run's own declared fields stay the ones it was admitted with when the plugin
    # is republished declaring another field.
    republished = FILL.format(declared='("bbb",)')
    assert isinstance(kernel.control.publish_plugin(republished), PublishView)
    again = kernel.control.project.status(admitted.run_id)
    assert again == admitted
    assert isinstance(again, RunView) and isinstance(again.summary, dict)
    assert "ccc" in again.summary


def test_summary_json_is_written_once(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(BIG_EARLY), PublishView)
    view = _run(kernel, "big_early")
    path = run_dir_for(kernel.home, view.run_id) / "evidence" / "summary.json"
    first = path.stat()
    first_bytes = path.read_bytes()

    for _ in range(3):
        again = kernel.control.project.status(view.run_id)
        assert again == view

    later = path.stat()
    assert (later.st_ino, later.st_mtime_ns) == (first.st_ino, first.st_mtime_ns)
    assert path.read_bytes() == first_bytes


def test_declared_fields_read_only_via_load_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # neither module reads the manifest itself
    for module in (project_module, projection):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        assert "manifest.json" not in source, module.__name__
        assert '"declared"' not in source, module.__name__

    # the projection follows what `load_declared` returns for the run's own snapshot
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(FILL.format(declared="()")), PublishView)
    seen: list[str] = []

    def fake_load_declared(snap):  # type: ignore[no-untyped-def]
        seen.append(snap.snapshot_id)
        return DeclaredMetadata(summary_fields=("ddd",))

    monkeypatch.setattr(project_module, "load_declared", fake_load_declared)
    with harness.patch_snapshot(kernel, "fill_plugin", summary_budget=FILL_BUDGET):
        view = _run(kernel, "fill_plugin")
    assert seen and set(seen) == {view_snapshot_id(kernel, view)}
    assert isinstance(view.summary, dict)
    assert "ddd" in view.summary


def view_snapshot_id(kernel, view: RunView) -> str:  # type: ignore[no-untyped-def]
    spec = run_dir_for(kernel.home, view.run_id) / "evidence" / "spec.json"
    return str(json.loads(spec.read_text("utf-8"))["snapshot_id"])
