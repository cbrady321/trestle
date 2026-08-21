"""M2 bounding tests — projection, fetch, limits, artifacts, pins."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from trestle.common import codes
from trestle.common.types import RequestOutcome, RunView
from trestle.server.pins import PinStore


@pytest.fixture
def bounded_kernel(kernel, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRESTLE_TEST_LIMITS", "1")
    kernel.registry.refresh()
    return kernel


def test_result_index_written_on_echo(bounded_kernel) -> None:
    view = bounded_kernel.control.run(plugin="echo", args={"message": "idx"}, wait_ms=5000)
    assert isinstance(view, RunView)
    run_dir = _run_dir(bounded_kernel.home, view.run_id)
    assert (run_dir / "evidence" / "result.index").exists()
    assert (run_dir / "evidence" / "summary.json").exists()


def test_projection_never_slurps_result_json(bounded_kernel) -> None:
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(self: Path) -> bytes:
        if self.name == "result.json":
            raise AssertionError("result.json must not be slurped for agent projection")
        return original_read_bytes(self)

    with patch.object(Path, "read_bytes", guarded_read_bytes):
        view = bounded_kernel.control.run(
            plugin="big_array",
            args={"count": 3000},
            wait_ms=10_000,
        )
    assert isinstance(view, RunView)
    assert view.truncated is True
    assert isinstance(view.summary, dict)
    assert view.summary["count"] == 3000
    assert "handle" in view.summary


def test_fetch_jsonpath_on_array_summary_handle(bounded_kernel) -> None:
    view = bounded_kernel.control.run(
        plugin="big_array",
        args={"count": 3000},
        wait_ms=10_000,
    )
    assert isinstance(view, RunView)
    handle = view.summary["handle"]
    fetched = bounded_kernel.control.fetch(
        handle,
        {"kind": "jsonpath", "expr": "$[*]"},
    )
    assert isinstance(fetched, dict)
    assert fetched["tag"] == "jsonpath_matches"
    assert len(fetched["values"]) >= 1


def test_auto_promote_outputs(bounded_kernel) -> None:
    view = bounded_kernel.control.run(
        plugin="outputs_writer",
        args={"name": "out.txt", "body": "promoted-by-kernel"},
        wait_ms=5000,
    )
    assert isinstance(view, RunView)
    assert view.artifact_count >= 1
    run_dir = _run_dir(bounded_kernel.home, view.run_id)
    artifacts = list((run_dir / "evidence" / "artifacts").iterdir())
    assert artifacts
    assert any(path.read_text(encoding="utf-8") == "promoted-by-kernel" for path in artifacts)


def test_pin_unpin_persistence(bounded_kernel) -> None:
    view = bounded_kernel.control.run(plugin="echo", wait_ms=5000)
    assert isinstance(view, RunView)
    target = view.run_id
    pin = bounded_kernel.control.pin(target)
    assert pin.code == codes.PIN_ACCEPTED
    store = PinStore.open(bounded_kernel.home)
    assert store.is_pinned(target)
    unpin = bounded_kernel.control.unpin(target)
    assert unpin.code == codes.UNPIN_ACCEPTED
    store = PinStore.open(bounded_kernel.home)
    assert not store.is_pinned(target)


def test_fetch_rejects_path_shaped_target(bounded_kernel) -> None:
    out = bounded_kernel.control.fetch("/tmp/evil", {"kind": "tail", "count": 10})
    assert isinstance(out, RequestOutcome)
    assert out.code == codes.INVALID_HANDLE


def test_hostile_plugin_limits_and_control_plane_responsive(bounded_kernel) -> None:
    view = bounded_kernel.control.run(
        plugin="hostile",
        args={"flood_lines": 300, "flood_events": 150},
        wait_ms=30_000,
    )
    assert isinstance(view, RunView)
    assert view.state == "succeeded"
    assert view.limits_exceeded is not None
    assert len(view.limits_exceeded) >= 1
    streams = {str(item["stream"]) for item in view.limits_exceeded}
    assert "stdout" in streams or "events" in streams

    start = time.monotonic()
    catalog = bounded_kernel.control.list_plugins()
    elapsed_ms = (time.monotonic() - start) * 1000
    assert elapsed_ms < 500
    assert "items" in catalog

    status = bounded_kernel.control.run(
        plugin="echo",
        args={"message": "still-alive"},
        wait_ms=5000,
    )
    assert isinstance(status, RunView)
    assert status.state == "succeeded"


def test_limits_exceeded_in_meta_and_ledger(bounded_kernel) -> None:
    view = bounded_kernel.control.run(
        plugin="hostile",
        args={"flood_lines": 100, "flood_events": 80},
        wait_ms=30_000,
    )
    assert isinstance(view, RunView)
    run_dir = _run_dir(bounded_kernel.home, view.run_id)
    meta = json.loads((run_dir / "evidence" / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("limits_exceeded")
    ledger = (run_dir / "evidence" / "ledger.ndjson").read_text(encoding="utf-8")
    assert "limit_exceeded" in ledger


def _run_dir(home: Path, run_id: str) -> Path:
    for month_dir in (home / "runs").iterdir():
        candidate = month_dir / run_id
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"run dir not found for {run_id}")
