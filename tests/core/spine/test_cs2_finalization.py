"""CS-2 finalization (L.CS-2.5; B2-C10): nothing writes the run's evidence or result after the
terminal row on the stop paths, because the supervisor finalizes only after the GroupStop is
recorded. The K-7 and K-9 (enforcement) statements are published in the docs."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.core.spine import support
from tests.proof import ancestry, harness, tolerances
from trestle.common import clock
from trestle.server.procident import GroupStop

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def _evidence_digest(run_dir: Path) -> dict[str, str]:
    """The sha256 of every file under evidence/ (result.json included), by relative path."""
    evidence = run_dir / "evidence"
    return {
        str(path.relative_to(evidence)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(evidence.rglob("*"))
        if path.is_file()
    }


def _observe_after_terminal(run_dir: Path) -> list[dict[str, str]]:
    """Digests of the evidence across the observation window that follows the terminal row."""
    seen: list[dict[str, str]] = []
    end = time.monotonic() + tolerances.SETTLE_LONG_S * 2
    while time.monotonic() < end:
        seen.append(_evidence_digest(run_dir))
        time.sleep(tolerances.POLL_S)
    return seen


def _stopped_run(how: str, *, stopper: object | None = None) -> tuple[Path, str]:
    kernel = support.spine_kernel()
    if stopper is not None:
        kernel.control.conductor.stopper = stopper  # type: ignore[assignment]
    if how == "deadline":
        with harness.patch_snapshot(kernel, "scribbler", timeout_s=support.SHORT_DEADLINE_S):
            order = support.admit_order(
                kernel, "scribbler", {"seconds": tolerances.JOIN_WAIT_S * 6}
            )
    else:
        order = support.admit_order(kernel, "scribbler", {"seconds": tolerances.JOIN_WAIT_S * 6})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    support.wait_ready(run_dir)
    assert support.wait_until(
        lambda: (run_dir / "evidence" / "late_session.log").exists(), tolerances.JOIN_WAIT_S
    )
    if how == "cancel":
        kernel.control.cancel(order.run_id)
    thread.join(timeout=support.SHORT_DEADLINE_S + tolerances.JOIN_WAIT_S + clock.stop_bound)
    assert not thread.is_alive(), "conductor never returned"
    return run_dir, order.run_id


@pytest.mark.proves(
    "WR-CANCEL-2",
    "WR-CANCEL-2:no-bytes-after-finalization-stop-paths",
    "core",
    "core",
    "PROC",
    "BOTH",
)
@pytest.mark.proves("WR-CON-6", "WR-CON-6:containment-both-kernels", "core", "core", "PROC", "BOTH")
def test_no_evidence_or_result_bytes_change_after_terminal() -> None:
    """The sha256 of evidence/** and result.json is constant across the observation window after
    the terminal row, for the cancel and the deadline variants: the writers are stopped, and the
    GroupStop recorded, before the run is finalized."""
    for how, terminal in (("cancel", "cancelled"), ("deadline", "timed_out")):
        run_dir, run_id = _stopped_run(how)
        with support.reaping(run_id):
            kinds = support.kinds(run_dir)
            assert kinds[-1] == terminal
            # the row of the stop precedes the finalization the bytes below are read after
            assert kinds.index("group_stop") < kinds.index("evidence_finalized")
            assert (run_dir / "evidence" / "late_session.log").exists()  # the writers did write
            seen = _observe_after_terminal(run_dir)
            assert len({tuple(sorted(d.items())) for d in seen}) == 1, (how, "bytes changed")
            assert not support.marked(run_id), (how, "a writer is still alive")


def test_a_survivor_that_writes_after_the_terminal_row_is_seen_by_this_check() -> None:
    """The negative control: a stopper that reports the tree gone but stops nothing leaves the
    writers running, and the same window shows evidence bytes changing after the terminal row."""
    # the deadline path, so that only the injected stopper stops (a cancel request also stops)
    run_dir, run_id = _stopped_run("deadline", stopper=lambda attribution: GroupStop(True, False))
    try:
        assert support.kinds(run_dir)[-1] == "timed_out"
        seen = _observe_after_terminal(run_dir)
        assert len({tuple(sorted(d.items())) for d in seen}) > 1
    finally:
        ancestry.reap(support.marked(run_id))


def _doc(name: str) -> str:
    return (REPO / "docs" / name).read_text(encoding="utf-8")


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-7", "core", "core", "INSPECT", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-9-enforcement", "core", "core", "INSPECT", "CI")
def test_k7_and_k9_enforcement_statements_are_published() -> None:
    """K-7 (docs/agents.md): a cancel or deadline stops the whole tree within `stop_bound`.
    K-9, enforcement half (docs/plugins.md): `ctx.deadline` is enforced, not advisory. The
    published numbers are the ones clock.py holds."""
    agents, plugins = _doc("agents.md"), _doc("plugins.md")
    for name in ("stop_bound", "grace", "kill", "poll_interval"):
        assert f"`{name}`" in agents, name
        assert f"`{name}`" in plugins, name
    assert "`ctx.deadline`" in plugins and re.search(r"ctx\.deadline`[^.]*enforced", plugins)
    assert "advisory" not in plugins.lower()
    assert "TRESTLE_CANCEL_GRACE_S" in agents and "TRESTLE_CANCEL_KILL_S" in agents
    # the numbers published are the module's own defaults (read in a clean environment)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRESTLE_CANCEL_")}
    env["PYTHONPATH"] = str(REPO)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from trestle.common import clock as c; "
            "print(c.grace, c.kill, c.stop_bound, c.poll_interval)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    grace, kill, bound, poll = (float(v) for v in out)
    assert f"`grace` ({grace:g} s)" in agents
    assert f"`kill` ({kill:g} s)" in agents
    assert f"`stop_bound` ({bound:g} s" in agents
    assert f"`poll_interval` ({poll:g} s)" in agents
    assert f"`grace` (default {grace:g} s)" in plugins
    assert f"`kill` (default {kill:g} s)" in plugins
    assert f"{bound:g} s by default" in plugins
    assert f"`poll_interval` ({poll:g} s)" in plugins
