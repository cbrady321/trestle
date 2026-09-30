"""Producers of the single-level fossil band (L.SL-11.2; MC-11, MC-26, CM-5).

`python -m tests.proof.fossils generate --checkpoint single --states all` runs the producers that
`tests/fixtures/fossils/single/MANIFEST.toml` names into `tests/fixtures/fossils/single/<state>/
home/{runs,snapshots,idempotency.json}`. The states are generated and committed by L.J-SINGLE.1
inside the checkpoint merge, never here: this leaf commits the producers and the MANIFEST only.

Each producer drives today's kernel over a workflow fixture through the harness's one bypass of
the temporary multi-vertex refusal (`harness.admit_tree`, MC-26: the run is admitted through
`write_admitted_run`, so its spec carries a `plan`) and asserts the state it was asked for. The
states are the writer's own single-level shapes a reader before SV-3 has to survive (the backward
straddle, TR-L) and the drain rehearsal reads (`inflight-plan-bearing`, the one state E-SV3-1's
`states` names, CM-9):

- `inflight-plan-bearing`: a plan-bearing one-vertex root that is non-terminal, holds a claimed
  and confirmed create (a created handle) in its lane and has no terminal row: a copy of the run
  directory taken while the run is polling, the crash image of a server killed at that moment.
- `terminal-passed`, `terminal-cancelled`, `terminal-timed-out`: the three terminal shapes of a
  plan-bearing one-vertex root, from real runs of the spine fixture (and, for the deadline, of an
  uncooperative unit, `hold_leaf`).
- `planless-started`: an S0-shaped root, whose implicit depth-1 plan names no declared tree
  (B2-C1), non-terminal: the backward straddle of a plain plugin under the plan-bearing host.

Every timing bound is the harness's or the published clock's (SA-05)."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.pins.a_lifecycle import fossil_producers as s0_producers
from tests.proof import ancestry, harness, records, tolerances
from trestle.common import clock
from trestle.common.types import RunView
from trestle.server.main import Kernel

ROOT = Path(__file__).resolve().parents[3]
BAND = "single"
WORKFLOWS = ROOT / "tests" / "fixtures" / "workflows"
SPINE_LEAF = WORKFLOWS / "spine_leaf.py"
HOLD_LEAF = WORKFLOWS / "hold_leaf.py"
ADVANCE = {"env": "dev", "mode": "advance"}
HANG = {"env": "dev", "mode": "hang"}
POLLS_SEEN = 3  # evidence events recorded by the wait after the create was confirmed
HOME_ENTRIES = ("runs", "snapshots", "idempotency.json", "service_epoch")


def _kernel(home: Path, fixture: Path) -> Kernel:
    """A kernel over `home` whose only plugin is `fixture` (so the fossil's snapshots directory
    holds one snapshot, not the whole shared catalog)."""
    plugin_dir = Path(tempfile.mkdtemp(prefix="fossil-plugin-"))
    shutil.copy(fixture, plugin_dir / fixture.name)
    return harness.fresh_kernel(plugin_dirs=[plugin_dir], home=home)


def _wait_until(predicate: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(tolerances.POLL_S)
    raise AssertionError(f"fossil producer: {what} did not happen within the join bound")


def _events(run_dir: Path) -> int:
    events = run_dir / "evidence" / "events.ndjson"
    return len(events.read_text(encoding="utf-8").splitlines()) if events.exists() else 0


def _confirmed(run_dir: Path) -> bool:
    """The lane holds a confirmed create (a claim and its confirmation: a created handle)."""
    return any(row.cls == "confirmation" for row in records.lane_rows(run_dir).rows)


class Polling:
    """True once the create is confirmed and the wait has recorded `POLLS_SEEN` more evidence
    events after it: the run is live, polling, and its lane is quiet (only the release walk writes
    to it next)."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.mark: int | None = None

    def __call__(self) -> bool:
        if self.mark is None:
            if not _confirmed(self.run_dir):
                return False
            self.mark = _events(self.run_dir)
        return _events(self.run_dir) >= self.mark + POLLS_SEEN


@contextmanager
def _reaping(run_id: str) -> Iterator[None]:
    """Every process whose argv carries the run id (a marker of at least eight characters) is
    killed on exit, whatever the producer did."""
    try:
        yield
    finally:
        ancestry.reap({p for p in ancestry.snapshot() if run_id in p.argv})


@contextmanager
def _fast_group_stop() -> Iterator[None]:
    """The host's group stop waits `grace` then `kill`; a generated fossil does not need the
    operator's patience. Applied after admission only, so the recorded plan is the published
    one."""
    grace, kill = clock.grace, clock.kill
    clock.grace = clock.kill = tolerances.SETTLE_SHORT_S
    try:
        yield
    finally:
        clock.grace, clock.kill = grace, kill


def _tidy(home: Path) -> None:
    """Byte-code caches are the interpreter's, not the state's: a fossil holds none."""
    for cache in sorted(home.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)


def _terminal_of(run_dir: Path) -> str | None:
    return records.node_record(run_dir).terminal


def _declared(run_dir: Path) -> bool:
    """The run's admitted plan names a declared tree (`declaration_digest` set): a plan-bearing
    root. A plain plugin's implicit depth-1 plan is recorded too (B2-C1) but names none."""
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    plan = spec.get("plan")
    return isinstance(plan, dict) and plan.get("declaration_digest") is not None


def _drive_live(
    kernel: Kernel, request: dict[str, Any], fixture: Path
) -> tuple[Any, threading.Thread, list[RunView]]:
    """Admit `fixture` through the harness and start its conductor on a thread: the run is live
    and plan-bearing (`spec.plan` is recorded by `write_admitted_run`)."""
    admitted = harness.admit_tree(fixture, request, kernel=kernel)
    views: list[RunView] = []
    conductor = threading.Thread(target=lambda: views.append(harness.drive_tree(admitted)))
    conductor.start()
    return admitted, conductor, views


def produce_terminal_passed(home: Path) -> None:
    """A one-vertex root that creates its marker, sees it ready and releases it."""
    kernel = _kernel(home, SPINE_LEAF)
    admitted = harness.admit_tree(SPINE_LEAF, ADVANCE, kernel=kernel)
    with _reaping(admitted.run_id):
        harness.drive_tree(admitted)
    assert _terminal_of(admitted.run_dir) == "succeeded"
    assert _declared(admitted.run_dir)
    _tidy(home)


def produce_terminal_cancelled(home: Path) -> None:
    """A one-vertex root cancelled mid-poll: the create was applied and the release walk gave it
    back."""
    kernel = _kernel(home, SPINE_LEAF)
    admitted, conductor, _ = _drive_live(kernel, HANG, SPINE_LEAF)
    with _reaping(admitted.run_id), _fast_group_stop():
        _wait_until(Polling(admitted.run_dir), "the wait's polls")
        kernel.control.cancel(admitted.run_id)
        conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
        assert not conductor.is_alive(), "the run never reached its terminal row"
    assert _terminal_of(admitted.run_dir) == "cancelled"
    assert _declared(admitted.run_dir)
    _tidy(home)


def produce_terminal_timed_out(home: Path) -> None:
    """A one-vertex root whose unit never returns: the release point (deadline minus the release
    slice) stops it, and the host's group stop ends the tree."""
    kernel = _kernel(home, HOLD_LEAF)
    admitted, conductor, _ = _drive_live(kernel, {"env": "dev"}, HOLD_LEAF)
    with _reaping(admitted.run_id), _fast_group_stop():
        conductor.join(timeout=clock.stop_bound + clock.finalization_margin)
        assert not conductor.is_alive(), "the run never reached its terminal row"
    assert _terminal_of(admitted.run_dir) == "timed_out"
    assert _declared(admitted.run_dir)
    _tidy(home)


def produce_inflight_plan_bearing(home: Path) -> None:
    """A non-terminal plan-bearing root with lane claims and a created handle: the run directory
    copied while the wait polls (the lane is quiet: plan, claim, confirmation), then the live run
    is cancelled and reaped. The copy has no terminal row."""
    scratch = Path(tempfile.mkdtemp(prefix="fossil-inflight-"))
    kernel = _kernel(scratch, SPINE_LEAF)
    admitted, conductor, _ = _drive_live(kernel, HANG, SPINE_LEAF)
    try:
        with _reaping(admitted.run_id), _fast_group_stop():
            _wait_until(Polling(admitted.run_dir), "the wait's polls")
            home.mkdir(parents=True, exist_ok=True)
            for name in HOME_ENTRIES:
                source = scratch / name
                if source.is_dir():
                    shutil.copytree(
                        source, home / name, ignore=shutil.ignore_patterns("__pycache__")
                    )
                elif source.is_file():
                    shutil.copy(source, home / name)
            kernel.control.cancel(admitted.run_id)
            conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
            assert not conductor.is_alive(), "the live run never ended"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    (copy,) = sorted((home / "runs").glob("*/r_*"))
    assert _terminal_of(copy) is None, "the copy already has a terminal row"
    rows = records.lane_rows(copy)
    assert not rows.problems and not rows.torn, rows.problems
    classes = [row.cls for row in rows.rows]
    assert "plan" in classes and "issue" in classes and "confirmation" in classes, classes
    assert _declared(copy)
    _tidy(home)


def produce_planless_started(home: Path) -> None:
    """An S0-shaped root (its plan names no declaration) that started and has no terminal row: the
    S0 `started` state written by today's kernel, the drain rule's control (a plan-less root is
    folded as before)."""
    s0_producers.produce_started(home)
    (run_dir,) = sorted((home / "runs").glob("*/r_*"))
    assert not _declared(run_dir) and _terminal_of(run_dir) is None
    _tidy(home)
