"""M5 query tests — views, paging, R-QB-28, conformance."""

from __future__ import annotations

import threading

import pytest

from trestle.common import codes
from trestle.common.types import AdmitRequest, RequestOutcome, RunView, WorkOrder
from trestle.query.conformance import semantic_items, validate_envelope
from trestle.query.views import VIEW_NAMES, VIEW_ROW_FIELDS


@pytest.fixture
def waiting_kernel(kernel, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")
    kernel.registry.refresh()
    return kernel


def _run_plugin(kernel, plugin: str, args: dict[str, object] | None = None) -> RunView:
    view = kernel.control.run(plugin=plugin, args=args or {}, wait_ms=5000)
    assert isinstance(view, RunView)
    return view


def test_all_nine_views_conform(kernel) -> None:
    view = _run_plugin(kernel, "echo", {"message": "query-conform"})
    for name in sorted(VIEW_NAMES):
        params: dict[str, object]
        if name in {"recent_runs", "recent_failures"}:
            params = {}
        elif name == "artifact_refs":
            params = {"artifact_id": "art_missing"}
        else:
            params = {"run_id": view.run_id}
        out = kernel.control.query(name, params)
        assert isinstance(out, dict), name
        assert out["backend"] == "filesystem"
        assert out["as_of"]
        errors = validate_envelope(name, out)
        assert errors == [], f"{name}: {errors}"
        for row in out["items"]:
            assert set(row.keys()) == VIEW_ROW_FIELDS[name]


def test_recent_runs_pagination(kernel) -> None:
    ids: list[str] = []
    for idx in range(3):
        view = _run_plugin(kernel, "echo", {"message": f"page-{idx}"})
        ids.append(view.run_id)

    first = kernel.control.query("recent_runs", {}, cursor=None)
    assert isinstance(first, dict)
    assert len(first["items"]) >= 1
    assert first["backend"] == "filesystem"
    assert first["as_of"]

    if first.get("next_cursor"):
        second = kernel.control.query("recent_runs", {}, cursor=str(first["next_cursor"]))
        assert isinstance(second, dict)
        first_ids = {row["run_id"] for row in first["items"]}
        second_ids = {row["run_id"] for row in second["items"]}
        assert first_ids.isdisjoint(second_ids)


def test_cursor_expired_on_stale_token(kernel) -> None:
    _run_plugin(kernel, "echo", {"message": "cursor"})
    first = kernel.control.query("recent_runs", {})
    assert isinstance(first, dict)
    stale = kernel.control.query("recent_runs", {}, cursor="q_stale")
    assert isinstance(stale, RequestOutcome)
    assert stale.code == codes.CURSOR_EXPIRED
    assert stale.retryable is True


def test_running_run_tail_not_finalized(waiting_kernel, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")
    waiting_kernel.registry.refresh()

    admit = waiting_kernel.control.admission.admit(
        AdmitRequest(plugin="slow", args={"seconds": 5.0})
    )
    assert admit.tag == "admitted"
    run_id = admit.run_id
    snap = waiting_kernel.registry.get("slow")
    assert snap is not None
    from trestle.server.ledger import RunLedger, ledger_path, run_dir_for

    ledger_path_value = ledger_path(run_dir_for(waiting_kernel.home, run_id))
    created = RunLedger.open(ledger_path_value).last_kind("created")
    assert created is not None
    order = WorkOrder(
        run_id=run_id,
        snapshot_id=snap.snapshot_id,
        spec_hash=str(created.get("spec_hash", "")),
    )
    thread = threading.Thread(
        target=waiting_kernel.control.conductor.drive,
        args=(order,),
        daemon=True,
    )
    thread.start()

    status = waiting_kernel.control.project.status(run_id)
    assert isinstance(status, RunView)
    assert status.state in {"queued", "running"}

    for blocked_view in ("run_tail", "run_events"):
        out = waiting_kernel.control.query(blocked_view, {"run_id": run_id})
        assert isinstance(out, RequestOutcome), blocked_view
        assert out.code == codes.NOT_FINALIZED
        assert out.retryable is True

    allowed = waiting_kernel.control.query("run", {"run_id": run_id})
    assert isinstance(allowed, dict)
    assert allowed["items"][0]["run_id"] == run_id


def test_query_echo_run_history_integration(kernel) -> None:
    view = _run_plugin(kernel, "echo", {"message": "history"})
    run_id = view.run_id

    run_rows = kernel.control.query("run", {"run_id": run_id})
    assert isinstance(run_rows, dict)
    assert run_rows["items"][0]["plugin"] == "echo"
    assert run_rows["items"][0]["state"] == "succeeded"

    events = kernel.control.query("run_events", {"run_id": run_id})
    assert isinstance(events, dict)
    assert len(events["items"]) >= 1
    assert events["items"][0]["kind"] == "log"

    recent = kernel.control.query("recent_runs", {})
    assert isinstance(recent, dict)
    recent_ids = [row["run_id"] for row in recent["items"]]
    assert run_id in recent_ids


def test_semantic_payload_excludes_backend_as_of(kernel) -> None:
    view = _run_plugin(kernel, "echo", {"message": "semantic"})
    first = kernel.control.query("run", {"run_id": view.run_id})
    second = kernel.control.query("run", {"run_id": view.run_id})
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert semantic_items(first) == semantic_items(second)
    assert first["backend"] == second["backend"] == "filesystem"


def test_invalid_view_rejected(kernel) -> None:
    out = kernel.control.query("plugin_stats", {})
    assert isinstance(out, RequestOutcome)
    assert out.code == codes.INVALID_VIEW


def test_run_provenance_fields(kernel) -> None:
    view = _run_plugin(kernel, "echo", {"message": "prov"})
    out = kernel.control.query("run_provenance", {"run_id": view.run_id})
    assert isinstance(out, dict)
    row = out["items"][0]
    assert row["run_id"] == view.run_id
    assert row["snapshot_id"]
    assert row["spec_hash"]
    assert row["args_hash"]
    assert row["source_sha256"]
