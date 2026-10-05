"""L.CL-B1.1: the result summary's budget is the run's own, declared fields are reserved first, and
a declared field that cannot fit says so (WR-TERM-5)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.core.answer.plugins import hundred_services
from tests.proof import harness, mcp_host, tolerances
from trestle.child.index import Index
from trestle.common import codes
from trestle.common.types import DeclaredMetadata, PublishView, RunView
from trestle.server import project as project_module
from trestle.server import projection
from trestle.server.ledger import RunLedger, ledger_path, run_dir_for
from trestle.server.projection import count_events

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


# ---- L.CL-B1.2: event_count on the ledger; the one-vertex hundred-service answer -------------

HUNDRED_DIR = Path(__file__).resolve().parent / "plugins"
MAX_PAGES = 200  # a bound on following a cursor, far above what ~10 MiB of logs needs


def _host_run(host: mcp_host.McpHost, mode: str) -> dict[str, Any]:
    shutil.copy(HUNDRED_DIR / "hundred_services.py", host.home / "plugins")
    answer = host.call(
        "run",
        {
            "plugin": "hundred_services",
            "args": {"mode": mode},
            "wait_ms": tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert isinstance(answer, dict) and "run_id" in answer, answer
    return answer


def _run_dir(host: mcp_host.McpHost, run_id: str) -> Path:
    (found,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    return found


def _log_volume(run_dir: Path) -> int:
    evidence = run_dir / "evidence"
    files = [evidence / "events.ndjson", *(evidence / "console").glob("*.log")]
    files += [p for p in (evidence / "artifacts").glob("*") if p.is_file()]
    return sum(p.stat().st_size for p in files if p.exists())


def _pages(host: mcp_host.McpHost, view: str, run_id: str) -> list[dict[str, Any]]:
    """Every row of a run-scoped view, following `next_cursor` until it ends."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        args: dict[str, Any] = {"view": view, "params": {"run_id": run_id}}
        if cursor is not None:
            args["cursor"] = cursor
        page = host.call("query", args)
        assert "items" in page, page
        rows.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows
    raise AssertionError(f"{view} did not end within {MAX_PAGES} pages")


def _assert_every_handle_fetches(
    host: mcp_host.McpHost, answer: dict[str, Any], run_dir: Path
) -> None:
    run_id = answer["run_id"]
    # the answer's own handle (only a run that returned a result has one)
    if answer.get("next"):
        fetched = host.call(
            "fetch", {"target": answer["next"], "window": {"kind": "jsonpath", "expr": "$.verdict"}}
        )
        assert fetched.get("values") == ["pass"], fetched
    # every artifact handle
    artifacts = _pages(host, "run_artifacts", run_id)
    assert len(artifacts) == hundred_services.ARTIFACT_COUNT
    for row in artifacts:
        assert row["state"] == "available"
        fetched = host.call(
            "fetch", {"target": row["artifact_id"], "window": {"kind": "head", "count": 16}}
        )
        assert "tag" in fetched and "code" not in fetched, fetched
    # the console and the events resolve to bounded pages that end
    assert _pages(host, "run_tail", run_id)
    assert _pages(host, "run_events", run_id)


@pytest.mark.proves(
    "WR-TERM-5", "WR-TERM-5:hundred-service-decisive-in-budget", "core", "core", "MCP", "CI"
)
@pytest.mark.proves("WR-TERM-5", "WR-TERM-5:every-handle-fetches", "core", "core", "MCP", "CI")
@pytest.mark.proves("WR-TERM-5", "A1.4", "A", "core", "MCP", "CI")
def test_hundred_service_answer_pass_and_fail(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        passed = _host_run(host, "pass")
        failed = _host_run(host, "fail")
        pass_dir = _run_dir(host, passed["run_id"])
        fail_dir = _run_dir(host, failed["run_id"])

        # the fixture leaves about ten MiB of logs, across console, events and artifacts
        for run_dir in (pass_dir, fail_dir):
            assert _log_volume(run_dir) >= 9 * MIB
            for stream in (run_dir / "evidence" / "events.ndjson",):
                assert stream.stat().st_size > MIB
            assert sum(p.stat().st_size for p in (run_dir / "evidence" / "console").glob("*")) > MIB
            assert (
                sum(p.stat().st_size for p in (run_dir / "evidence" / "artifacts").glob("*")) > MIB
            )

        # pass: the class and the decisive fields, within the run's own budget
        assert passed["state"] == "succeeded"
        assert passed["outcome"]["class"] == "passed"
        summary = passed["summary"]
        assert summary["verdict"] == "pass"
        assert summary["failed_services"] == []
        assert summary["service_count"] == hundred_services.SERVICE_COUNT
        assert "services" not in summary  # the hundred records stay behind the handle
        assert passed["truncated"] is True and "services" in passed["omitted"]
        spec = json.loads((pass_dir / "evidence" / "spec.json").read_text("utf-8"))
        assert (
            len(json.dumps(summary, separators=(",", ":")).encode("utf-8"))
            <= spec["summary_budget"]
        )
        frame_bytes = len(json.dumps(passed, separators=(",", ":")).encode("utf-8"))
        assert frame_bytes < 16 * 1024  # the whole answer is small next to ten MiB of logs

        # fail: the class, the code and a bounded explanation
        assert failed["state"] == "failed"
        assert failed["outcome"]["class"] == "execution_error"
        assert failed["outcome"]["code"] == codes.EXECUTION_PLUGIN_RAISED
        assert hundred_services.service_name(hundred_services.UNHEALTHY) in json.dumps(
            failed["error"]
        )
        assert len(json.dumps(failed, separators=(",", ":")).encode("utf-8")) < 16 * 1024

        # every handle either answer names fetches
        _assert_every_handle_fetches(host, passed, pass_dir)
        _assert_every_handle_fetches(host, failed, fail_dir)


def test_event_count_is_recorded_on_evidence_finalized_and_read_from_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = harness.fresh_kernel(home=tmp_path / "home")
    view = kernel.control.run(
        plugin="echo", args={"message": "count"}, wait_ms=tolerances.HARNESS_WAIT_MS
    )
    assert isinstance(view, RunView) and view.state == "succeeded"
    run_dir = run_dir_for(kernel.home, view.run_id)
    lines = [
        line
        for line in (run_dir / "evidence" / "events.ndjson").read_text("utf-8").splitlines()
        if line.strip()
    ]
    row = RunLedger.open(ledger_path(run_dir)).last_kind("evidence_finalized")
    assert row is not None
    assert (
        row["event_count"] == len(lines) == count_events(run_dir / "evidence") == view.event_count
    )

    # once finalized, a poll reads the count from the row: it never opens the events file
    def refuse(_evidence: Path) -> int:
        raise AssertionError("event_count must come from the ledger row, not a file scan")

    monkeypatch.setattr(project_module, "count_events", refuse)
    again = kernel.control.project.status(view.run_id)
    assert isinstance(again, RunView) and again.event_count == view.event_count
