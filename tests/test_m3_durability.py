"""M3 durability tests — cancel, timeout, recovery, torn ledger."""

from __future__ import annotations

import dataclasses
import json
import threading
import time

import pytest

from trestle.common import codes
from trestle.common.types import AdmitRequest, RunView, WorkOrder
from trestle.server.home import live_servers, marker_path
from trestle.server.ledger import RunLedger, ledger_path, run_dir_for
from trestle.server.main import create_kernel
from trestle.server.recovery import (
    recover_run_dir,
    seed_interrupted_run,
    sweep_run_dir,
)
from trestle.server.runs import RunRegistry


@pytest.fixture
def durable_kernel(kernel, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")
    kernel.registry.refresh()
    _patch_slow_timeout(kernel, timeout_s=2)
    return kernel


def _patch_slow_timeout(kernel, *, timeout_s: int) -> None:
    snap = kernel.registry.get("slow")
    if snap is not None:
        kernel.registry.snapshots["slow"] = dataclasses.replace(snap, timeout_s=timeout_s)


def test_cancel_accepted_and_terminal_cancelled(durable_kernel) -> None:
    # the fixture's 2 s deadline would race the cancel below on a loaded runner: this run's
    # deadline is well past the cancel, so the cancel alone decides the class
    _patch_slow_timeout(durable_kernel, timeout_s=30)
    admit = durable_kernel.control.admission.admit(
        AdmitRequest(plugin="slow", args={"seconds": 60.0})
    )
    assert admit.tag == "admitted"
    run_id = admit.run_id
    snap = durable_kernel.registry.get("slow")
    assert snap is not None
    created = RunLedger.open(ledger_path(run_dir_for(durable_kernel.home, run_id))).last_kind(
        "created"
    )
    assert created is not None
    order = WorkOrder(
        run_id=run_id,
        snapshot_id=snap.snapshot_id,
        spec_hash=str(created.get("spec_hash", "")),
    )

    thread = threading.Thread(
        target=durable_kernel.control.conductor.drive,
        args=(order,),
        daemon=True,
    )
    thread.start()
    time.sleep(0.3)

    cancel = durable_kernel.control.cancel(run_id)
    assert cancel.code == codes.CANCEL_ACCEPTED
    thread.join(timeout=15)
    assert not thread.is_alive()

    view = durable_kernel.control.project.status(run_id)
    assert isinstance(view, RunView)
    assert view.state == "cancelled"


def test_timeout_terminal_state(durable_kernel) -> None:
    view = durable_kernel.control.run(
        plugin="slow",
        args={"seconds": 30.0},
        wait_ms=10_000,
    )
    assert isinstance(view, RunView)
    assert view.state == "timed_out"


def test_recovery_first_pass_interrupted(durable_kernel) -> None:
    run_id = "r_test_recovery_started"
    run_dir = seed_interrupted_run(durable_kernel.home, run_id, last_kind="started")
    recover_run_dir(run_dir)

    ledger = RunLedger.open(ledger_path(run_dir))
    assert ledger.has_kind("evidence_finalized")
    assert ledger.has_kind("interrupted")
    assert ledger.projected_state() == "interrupted"
    assert (run_dir / "evidence" / "meta.json").exists()


def test_recovery_second_pass_idempotent(durable_kernel) -> None:
    run_id = "r_test_recovery_idempotent"
    run_dir = seed_interrupted_run(durable_kernel.home, run_id, last_kind="execution_ended")
    recover_run_dir(run_dir)
    first_len = len(RunLedger.open(ledger_path(run_dir)).records)

    recover_run_dir(run_dir)
    second = RunLedger.open(ledger_path(run_dir))
    assert len(second.records) == first_len
    assert second.projected_state() == "interrupted"


def test_recovery_terminal_present_rematerializes_meta_only(durable_kernel) -> None:
    run_id = "r_test_recovery_terminal"
    run_dir = seed_interrupted_run(durable_kernel.home, run_id, last_kind="started")
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append(
        "execution_ended",
        run_id=run_id,
        classification="failed",
        exit_code=1,
        duration_ms=1,
    )
    ledger.append(
        "evidence_finalized",
        run_id=run_id,
        completeness="partial",
        result_state="absent",
    )
    ledger.append("failed", run_id=run_id)
    before = len(ledger.records)

    recover_run_dir(run_dir)
    after = RunLedger.open(ledger_path(run_dir))
    assert len(after.records) == before
    meta = json.loads((run_dir / "evidence" / "meta.json").read_text(encoding="utf-8"))
    assert meta["classification"] == "failed"


def test_recovery_no_ledger_sweeps_run_dir(durable_kernel) -> None:
    run_dir = durable_kernel.home / "runs" / "2099-02" / "r_orphan_no_ledger"
    run_dir.mkdir(parents=True)
    (run_dir / "evidence").mkdir()
    (run_dir / "evidence" / "meta.json").write_text("{}", encoding="utf-8")

    sweep_run_dir(run_dir)
    assert not run_dir.exists()


def test_torn_ledger_line_tolerance_on_read(durable_kernel) -> None:
    run_id = "r_test_torn_ledger"
    run_dir = seed_interrupted_run(durable_kernel.home, run_id, last_kind="started")
    path = ledger_path(run_dir)
    path.write_text(path.read_text(encoding="utf-8") + '{"kind":"broken"\n', encoding="utf-8")

    ledger = RunLedger.open(path)
    assert ledger.has_kind("started")
    recover_run_dir(run_dir)
    assert RunLedger.open(path).projected_state() == "interrupted"


def test_recover_on_startup_new_epoch_and_sweep(durable_kernel, plugin_dir) -> None:
    """v0.3.1: a start reaps (the service epoch is gone): a new kernel has its own server id, and
    its start pass finalizes a run whose owner is dead, removing the run's live marker."""
    run_id = "r_test_startup_recovery"
    seed_interrupted_run(durable_kernel.home, run_id, last_kind="admitted")

    restarted = create_kernel(home=durable_kernel.home, plugin_dirs=[plugin_dir])
    assert restarted.server_id != durable_kernel.server_id
    ledger = RunLedger.open(ledger_path(durable_kernel.home / "runs" / "2099-01" / run_id))
    assert ledger.projected_state() == "interrupted"
    assert not marker_path(durable_kernel.home, run_id).exists()
    assert live_servers(durable_kernel.home) == []  # only a serving process registers


def test_run_registry_cancel_flag(durable_kernel) -> None:
    run_id = "r_test_registry_flag"
    run_dir = seed_interrupted_run(durable_kernel.home, run_id, last_kind="started")
    registry = RunRegistry()
    registry.request_cancel(run_id, run_dir)
    assert (run_dir / "work" / "cancel.flag").exists()
