"""CK-8 success reap (L.CK-8.1; K-8, A8.2, B2-C10): a run that succeeded leaves no process it
started, and nothing it started writes after the terminal row. The kill is B2-C10's, on every
terminal path; `conductor.REAP_ON_SUCCESS` is the switch of the K-8 decline patch (MC-CORE-12) and
the one thing this leaf adds."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from tests.core.spine import support
from tests.proof import tolerances
from trestle.common import clock
from trestle.common.types import RunView
from trestle.server import conductor
from trestle.server.main import Kernel
from trestle.server.procident import Attribution, GroupStop

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "docs" / "agents.md"


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def _digest(run_dir: Path) -> str:
    """One sha256 over every file under evidence/ (result.json included), path and bytes."""
    h = hashlib.sha256()
    for file in sorted((run_dir / "evidence").rglob("*")):
        if file.is_file():
            h.update(str(file.relative_to(run_dir)).encode())
            h.update(file.read_bytes())
    return h.hexdigest()


def _succeed_leaving_daemons(kernel: Kernel) -> tuple[str, Path, threading.Thread, set[int]]:
    """Start the `daemons` run, wait until both daemons are alive, writing and in the run's
    identity rows (so attribution does not race the plugin's exit), then let the plugin return."""
    order = support.admit_order(kernel, "daemons", {"seconds": tolerances.JOIN_WAIT_S * 6})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    support.wait_ready(run_dir)
    daemons = {p.pid for p in support.marked(order.run_id) if "daemon_" in p.argv}
    assert len(daemons) == 2, daemons
    assert support.wait_until(
        lambda: daemons <= {int(r["pid"]) for r in support.rows_of(run_dir, "process_identity")},
        tolerances.JOIN_WAIT_S,
    ), "the daemons never reached the run's identity rows"
    (run_dir / "work" / "tmp" / "go").write_text("1", encoding="utf-8")
    return order.run_id, run_dir, thread, daemons


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
    assert not thread.is_alive(), "conductor never returned"


@pytest.mark.proves("A8.2", "A8.2", "A", "core", "PROC", "BOTH")
@pytest.mark.proves("WR-OWN-3", "WR-OWN-3:success-no-survivor", "core", "core", "PROC", "BOTH")
def test_passed_run_leaves_no_attributable_process() -> None:
    """A plugin starts an in-group daemon and a setsid'd daemon that keep writing into evidence/
    and returns: after the answer, the run's ancestry is empty within stop_bound + tolerance."""
    kernel = support.spine_kernel()
    marker = ""
    try:
        run_id, run_dir, thread, daemons = _succeed_leaving_daemons(kernel)
        marker = run_id
        _join(thread)
        assert support.kinds(run_dir)[-1] == "succeeded"
        assert support.wait_until(
            lambda: not support.marked(run_id), clock.stop_bound + tolerances.PROC_WAIT_S
        ), support.marked(run_id)
        view = kernel.control.project.status(run_id)
        assert isinstance(view, RunView) and view.state == "succeeded"
        assert view.cleanup is not None and view.cleanup.processes == "released"
        (row,) = support.rows_of(run_dir, "group_stop")
        assert row["confirmed_gone"] is True and row["method"] == "signal"
        # both daemons were stopped through identity rows written before the first signal
        recorded = {int(r["pid"]) for r in support.rows_of(run_dir, "process_identity")}
        assert daemons <= recorded
    finally:
        if marker:
            from tests.proof import ancestry

            ancestry.reap(support.marked(marker))


@pytest.mark.proves(
    "WR-CANCEL-2",
    "WR-CANCEL-2:success-no-bytes-after-finalization",
    "core",
    "core",
    "PROC",
    "BOTH",
)
def test_success_no_bytes_after_finalization() -> None:
    """The sha256 of evidence/** and result.json is constant across the observation window that
    follows the terminal row, though both daemons wrote until the run ended."""
    kernel = support.spine_kernel()
    marker = ""
    try:
        run_id, run_dir, thread, _ = _succeed_leaving_daemons(kernel)
        marker = run_id
        wrote = [
            (run_dir / "evidence" / f"daemon_{n}.log").stat().st_size
            for n in ("in_group", "setsid")
        ]
        assert all(size > 0 for size in wrote), "the daemons never wrote before the run ended"
        _join(thread)
        assert support.kinds(run_dir)[-1] == "succeeded"
        assert (run_dir / "evidence" / "result.json").is_file()
        window = tolerances.SETTLE_LONG_S * 3
        seen = {_digest(run_dir)}
        end = time.monotonic() + window
        while time.monotonic() < end:
            seen.add(_digest(run_dir))
            time.sleep(tolerances.POLL_S)
        assert len(seen) == 1, "bytes under evidence/ changed after the terminal row"
    finally:
        if marker:
            from tests.proof import ancestry

            ancestry.reap(support.marked(marker))


def test_reap_on_success_switch_is_read_at_the_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The K-8 switch reaches the success path's call and no other: declined, a succeeded run is
    observed and never signalled (its group_stop is honest, so cleanup is `unknown`), while a run
    that failed is still stopped, because B2-C10 fixes the kill on every other terminal path."""
    from tests.proof import ancestry

    monkeypatch.setattr(conductor, "REAP_ON_SUCCESS", False)
    stopped: list[Attribution] = []

    def spy(attribution: Attribution) -> GroupStop:
        stopped.append(attribution)
        return GroupStop(confirmed_gone=True, signalled=False)

    kernel = support.spine_kernel()
    kernel.control.conductor.stopper = spy
    marker = ""
    try:
        run_id, run_dir, thread, _ = _succeed_leaving_daemons(kernel)
        marker = run_id
        _join(thread)
        assert support.kinds(run_dir)[-1] == "succeeded"
        assert stopped == []
        (row,) = support.rows_of(run_dir, "group_stop")
        assert row["confirmed_gone"] is False and row["method"] == "exit"
        view = kernel.control.project.status(run_id)
        assert isinstance(view, RunView)
        assert view.cleanup is not None and view.cleanup.processes == "unknown"
    finally:
        if marker:
            ancestry.reap(support.marked(marker))
    # a run that ended failed is stopped whatever the switch says
    kernel = support.spine_kernel()
    kernel.control.conductor.stopper = spy
    order = support.admit_order(
        kernel, "leaver", {"seconds": tolerances.JOIN_WAIT_S * 6, "fail": True}
    )
    thread = support.drive_in_thread(kernel, order)
    _join(thread)
    try:
        assert len(stopped) == 1
    finally:
        ancestry.reap(support.marked(order.run_id))


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-8", "core", "core", "INSPECT", "CI")
def test_k8_block_is_documented_and_the_switch_defaults_on() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert text.count("<!-- K-8 -->") == 1 and text.count("<!-- /K-8 -->") == 1
    block = text.split("<!-- K-8 -->")[1].split("<!-- /K-8 -->")[0]
    assert "K-8" in block and "REAP_ON_SUCCESS" in block and "succeeded" in block
    assert conductor.REAP_ON_SUCCESS is True
