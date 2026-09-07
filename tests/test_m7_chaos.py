"""M7 chaos matrix tests (R-VER-3)."""

from __future__ import annotations

import os
import textwrap
import time
from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.types import AdmitRequest, RequestOutcome, RunView
from trestle.server.config import load_config
from trestle.server.doctor import build_doctor_report, run_recover
from trestle.server.gc import run_gc
from trestle.server.idempotency import IdempotencyStore, rebuild_from_ledgers
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import create_kernel
from trestle.server.recovery import recover_run_dir, seed_interrupted_run


@pytest.fixture
def ops_kernel(kernel, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRESTLE_METADATA_DAYS", "0")
    monkeypatch.setenv("TRESTLE_ARTIFACT_DAYS", "0")
    monkeypatch.setenv("TRESTLE_ABANDONED_HOURS", "0")
    kernel.registry.refresh()
    return kernel


def test_recovery_twice_idempotent(ops_kernel) -> None:
    run_id = "r_chaos_recovery_twice"
    run_dir = seed_interrupted_run(ops_kernel.home, run_id, last_kind="started")
    recover_run_dir(run_dir)
    first_len = len(RunLedger.open(ledger_path(run_dir)).records)

    recover_run_dir(run_dir)
    second = RunLedger.open(ledger_path(run_dir))
    assert len(second.records) == first_len
    assert second.projected_state() == "interrupted"


def test_torn_ledger_line_recovery(ops_kernel) -> None:
    run_id = "r_chaos_torn_ledger"
    run_dir = seed_interrupted_run(ops_kernel.home, run_id, last_kind="started")
    path = ledger_path(run_dir)
    path.write_text(path.read_text(encoding="utf-8") + '{"kind":"broken"\n', encoding="utf-8")

    recover_run_dir(run_dir)
    assert RunLedger.open(path).projected_state() == "interrupted"


def test_idempotency_after_restart(ops_kernel) -> None:
    key = "chaos-idem-key"
    first = ops_kernel.control.run(
        plugin="echo",
        args={"message": "idem"},
        wait_ms=5000,
        idempotency_key=key,
    )
    assert isinstance(first, RunView)
    assert first.state == "succeeded"

    restarted = create_kernel(
        home=ops_kernel.home,
        plugin_dirs=[Path(__file__).resolve().parent / "fixtures" / "plugins"],
        skip_recovery=True,
    )
    second = restarted.control.run(
        plugin="echo",
        args={"message": "idem"},
        wait_ms=5000,
        idempotency_key=key,
    )
    assert isinstance(second, RunView)
    assert second.run_id == first.run_id


def test_idempotency_rebuild_from_ledger(ops_kernel) -> None:
    key = "ledger-rebuild-key"
    view = ops_kernel.control.run(
        plugin="echo",
        args={"message": "rebuild"},
        wait_ms=5000,
        idempotency_key=key,
    )
    assert isinstance(view, RunView)
    (ops_kernel.home / "idempotency.json").unlink(missing_ok=True)

    rebuild_from_ledgers(ops_kernel.home, ttl_s=3600)
    store = IdempotencyStore.open(ops_kernel.home)
    record = store.lookup(key)
    assert record is not None
    assert record.run_id == view.run_id


def test_idempotency_key_conflict(ops_kernel) -> None:
    key = "conflict-key"
    first = ops_kernel.control.admission.admit(
        AdmitRequest(
            plugin="echo",
            args={"message": "a"},
            idempotency_key=key,
        )
    )
    assert first.tag == "admitted"
    conflict = ops_kernel.control.admission.admit(
        AdmitRequest(
            plugin="echo",
            args={"message": "b"},
            idempotency_key=key,
        )
    )
    assert conflict.tag == "refused"
    assert conflict.outcome.code == codes.IDEMPOTENCY_KEY_CONFLICT


def test_sigterm_drain_refuses_new_admits(ops_kernel) -> None:
    ops_kernel.control.scheduler.draining = True
    result = ops_kernel.control.run(plugin="echo", args={"message": "blocked"})
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.SERVICE_DRAINING
    assert result.retryable is True


def test_fetch_rejects_filesystem_path(ops_kernel) -> None:
    result = ops_kernel.control.fetch("/etc/passwd", {"kind": "head", "count": 1})
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.INVALID_HANDLE


def test_gc_respects_pins(ops_kernel) -> None:
    view = ops_kernel.control.run(plugin="echo", args={"message": "pin-me"}, wait_ms=5000)
    assert isinstance(view, RunView)
    ops_kernel.control.pin(view.run_id)
    run_dir = _run_dir(ops_kernel.home, view.run_id)
    old = time.time() - 86400 * 400
    os.utime(run_dir, (old, old))

    report = run_gc(ops_kernel.home, load_config(ops_kernel.home))
    assert report.runs_removed == 0
    assert run_dir.exists()

    ops_kernel.control.unpin(view.run_id)
    report = run_gc(ops_kernel.home, load_config(ops_kernel.home))
    assert report.runs_removed >= 1
    assert not run_dir.exists()


def test_recover_cli_integrates_gc_and_epoch(
    ops_kernel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_interrupted_run(ops_kernel.home, "r_cli_recover", last_kind="admitted")
    old_epoch = (ops_kernel.home / "service_epoch").read_text(encoding="utf-8")
    code = run_recover(home=str(ops_kernel.home))
    assert code == 0
    out = capsys.readouterr().out
    assert "recovery complete" in out
    assert "service_epoch:" in out
    new_epoch = (ops_kernel.home / "service_epoch").read_text(encoding="utf-8")
    assert new_epoch != old_epoch


def test_doctor_reports_epoch_registry_and_runs(ops_kernel) -> None:
    view = ops_kernel.control.run(plugin="echo", args={"message": "doc"}, wait_ms=5000)
    assert isinstance(view, RunView)
    plugin_dirs = [ops_kernel.registry.plugin_dirs[0]]
    report = build_doctor_report(home=ops_kernel.home, plugin_dirs=plugin_dirs)
    assert report.service_epoch
    assert report.registry_version >= 1
    assert report.run_counts["total"] >= 1
    assert report.run_counts.get("succeeded", 0) >= 1
    assert report.draining is False
    assert "echo" in report.plugins


def test_run_admission_refresh_on_drop_in(ops_kernel, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    echo_src = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo.py"
    (plugin_dir / "echo.py").write_text(echo_src.read_text(encoding="utf-8"), encoding="utf-8")
    kernel = create_kernel(home=ops_kernel.home, plugin_dirs=[plugin_dir], skip_recovery=True)
    assert "drop_in" not in kernel.registry.snapshots

    drop_in = textwrap.dedent(
        """
        from trestle.plugin.surface import Context, trestle


        @trestle
        def drop_in(ctx: Context, value: str = "ok") -> dict[str, str]:
            return {"value": value}
        """
    )
    (plugin_dir / "drop_in.py").write_text(drop_in, encoding="utf-8")

    view = kernel.control.run(plugin="drop_in", args={"value": "fresh"}, wait_ms=5000)
    assert isinstance(view, RunView)
    assert view.state == "succeeded"
    assert view.summary == {"value": "fresh"}


def _run_dir(home: Path, run_id: str) -> Path:
    for month_dir in (home / "runs").iterdir():
        candidate = month_dir / run_id
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"run dir not found for {run_id}")
