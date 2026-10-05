"""Producers of the tree-lift fossil bands `tree-trl` (L.TR-L.11) and `tree-tr5` (L.TR-5.4; MC-11,
MC-B3-05, CM-5).

`python -m tests.proof.fossils generate --checkpoint tree-trl --states <ids>` runs the producers
that `tests/fixtures/fossils/tree-trl/MANIFEST.toml` names into
`tests/fixtures/fossils/tree-trl/<state>/home/{runs,snapshots,idempotency.json,service_epoch}`, and
`--checkpoint slice-a` regenerates them under `slice-a/` with the same ids at L.J-SLICE-A.1
(`tree-tr5` is the same, over `tests/fixtures/fossils/tree-tr5/MANIFEST.toml`; name the ids, since
`--states all` selects every band's states, and give `--fossils-root` as an absolute path: the run's
home is a path its children resolve). The runs are written by head: each goes through the host's
own admission (`ControlSurface.run`; the lifted refusal admits an `AllDeclaration` tree), so its
spec carries a `plan` that names a declared tree with more than one vertex, which no reader before
TR-L has ever seen. Nothing here reaches `tests/proof/harness.py`'s admission bypass.

The states (ids unique across every band's MANIFEST):

- `trl-inflight-<fixture>`: a non-terminal root. The run directory is copied while every leaf of
  `readiness_sibling` has created its marker and is polling (the lane is quiet: plan, claims and
  confirmations, no terminal row), then the live run is cancelled and reaped: the crash image of a
  server killed at that moment.
- `trl-terminal-<fixture>`: a finalized root (`exception_branch`, `upstream_covered`); the manifest
  expectation is the root projection only.
- `trl-views-<fixture>`: the same finalized homes; the expectation is their child views only (a
  reader before TR-2 serves none, E-TR-1). Each is its own real run of the same fixture, so a
  home is never shared between two states.

The `tree-tr5` band (L.TR-5.4) is the same three kinds over ChoiceNode roots (V-14), whose selection
no reader before TR-5 has seen: `tr5-inflight-choice_long_running` (copied while the selected
alternative is mid-step, then cancelled and reaped), `tr5-terminal-live_state` (a finalized root,
`healthy`: both choices selected, `passed`) and `tr5-views-live_state` (its own real run of the same
fixture; the expectation is its child views only).

Every timing bound is the harness's or the published clock's (SA-05)."""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import ancestry, harness, records, tolerances
from trestle.common import clock
from trestle.common.types import RequestOutcome, RunView
from trestle.server.main import Kernel

ROOT = Path(__file__).resolve().parents[2]
TREES = ROOT / "tests" / "fixtures" / "trees"
BAND = "tree-trl"
ARGS = {"env": "dev"}
LIVE_STATE_ARGS = {"env": "dev", "case": "healthy"}  # every node converges, both choices select
HOME_ENTRIES = ("runs", "snapshots", "idempotency.json", "service_epoch")
POLLS_SEEN = 3  # evidence events the waits record after the last create was confirmed
TERMINAL_WAIT_MS = int(tolerances.JOIN_WAIT_S * 6 * 1000)
INFLIGHT_LEAVES = 3  # `readiness_sibling`'s leaves that create a marker (waiter, w1, w2)
CHOICE_INFLIGHT_LEAVES = 1  # `choice_long_running`'s one selected alternative creates a marker


def _kernel(home: Path, fixture: str) -> Kernel:
    """A kernel over `home` whose only plugin is `tests/fixtures/trees/<fixture>.py`, so the
    fossil's snapshots directory holds one snapshot, not the whole shared catalog."""
    plugin_dir = Path(tempfile.mkdtemp(prefix="fossil-plugin-"))
    shutil.copy(TREES / f"{fixture}.py", plugin_dir / f"{fixture}.py")
    return harness.fresh_kernel(plugin_dirs=[plugin_dir], home=home)


def _wait_until(predicate: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(tolerances.POLL_S)
    raise AssertionError(f"fossil producer: {what} did not happen within the join bound")


def _run_dirs(home: Path) -> list[Path]:
    return sorted((home / "runs").glob("*/r_*"))


def _events(run_dir: Path) -> int:
    events = run_dir / "evidence" / "events.ndjson"
    return len(events.read_text(encoding="utf-8").splitlines()) if events.exists() else 0


def _confirmations(run_dir: Path) -> int:
    return sum(1 for row in records.lane_rows(run_dir).rows if row.cls == "confirmation")


def _terminal_of(run_dir: Path) -> str | None:
    return records.node_record(run_dir).terminal


class Polling:
    """True once every leaf's create is confirmed and the waits have recorded `POLLS_SEEN` more
    evidence events after that: the run is live, polling, and its lane is quiet (only the release
    walk writes to it next)."""

    def __init__(self, run_dir: Path, leaves: int) -> None:
        self.run_dir = run_dir
        self.leaves = leaves
        self.mark: int | None = None

    def __call__(self) -> bool:
        if self.mark is None:
            if _confirmations(self.run_dir) < self.leaves:
                return False
            self.mark = _events(self.run_dir)
        return _events(self.run_dir) >= self.mark + POLLS_SEEN


@contextmanager
def _reaping(home: Path) -> Iterator[None]:
    """Every process whose argv names a run under `home` (the run id is at least eight characters)
    is killed on exit, whatever the producer did."""
    try:
        yield
    finally:
        ids = {p.name for p in _run_dirs(home)}
        ancestry.reap({p for p in ancestry.snapshot() if any(i in p.argv for i in ids)})


@contextmanager
def _fast_group_stop() -> Iterator[None]:
    """The host's group stop waits `grace` then `kill`; a generated fossil does not need the
    operator's patience. Applied after admission only, so the recorded plan is the published one."""
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


def _plan_bearing_tree(run_dir: Path) -> None:
    """The root's admitted plan names a declared tree of more than one vertex."""
    import json

    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    plan = spec.get("plan")
    assert isinstance(plan, dict) and plan.get("declaration_digest") is not None, plan
    assert len(plan.get("vertices", [])) > 1, "not a multi-vertex root"


def _finalized(
    home: Path,
    fixture: str,
    expected_terminals: tuple[str, ...],
    args: dict[str, str] | None = None,
) -> None:
    """One run of `fixture` through the host to its finalized terminal row."""
    kernel = _kernel(home, fixture)
    with _reaping(home):
        view = kernel.control.run(
            plugin=fixture,
            args=dict(ARGS if args is None else args),
            wait_ms=TERMINAL_WAIT_MS,
            completion="terminal",
        )
        assert not isinstance(view, RequestOutcome), view
        assert isinstance(view, RunView), view
    (run_dir,) = _run_dirs(home)
    assert _terminal_of(run_dir) in expected_terminals, _terminal_of(run_dir)
    _plan_bearing_tree(run_dir)
    _tidy(home)


def produce_trl_terminal_exception_branch(home: Path) -> None:
    """A finalized root whose raiser's exception stopped its two siblings (the terminal row is the
    process state; the answer's outcome is `execution_error`)."""
    _finalized(home, "exception_branch", ("succeeded", "failed"))


def produce_trl_terminal_upstream_covered(home: Path) -> None:
    """A finalized root of two nodes ordered by a covered precondition: the producer creates, the
    consumer runs after it, the tree passes."""
    _finalized(home, "upstream_covered", ("succeeded",))


def _inflight(home: Path, fixture: str, leaves: int) -> None:
    """A non-terminal multi-vertex root: `fixture` copied while its `leaves` created markers are
    polling a readiness that never comes, then the live run is cancelled and reaped. The copy has no
    terminal row."""
    scratch = Path(tempfile.mkdtemp(prefix="fossil-inflight-"))
    kernel = _kernel(scratch, fixture)
    views: list[Any] = []

    def drive() -> None:
        views.append(
            kernel.control.run(
                plugin=fixture,
                args=dict(ARGS),
                wait_ms=TERMINAL_WAIT_MS,
                completion="terminal",
            )
        )

    conductor = threading.Thread(target=drive)
    conductor.start()
    try:
        with _reaping(scratch), _fast_group_stop():
            _wait_until(lambda: bool(_run_dirs(scratch)), "the admission of the tree")
            (live,) = _run_dirs(scratch)
            _wait_until(Polling(live, leaves), "the waits' polls")
            home.mkdir(parents=True, exist_ok=True)
            for name in HOME_ENTRIES:
                source = scratch / name
                if source.is_dir():
                    shutil.copytree(
                        source, home / name, ignore=shutil.ignore_patterns("__pycache__")
                    )
                elif source.is_file():
                    shutil.copy(source, home / name)
            kernel.control.cancel(live.name)
            conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
            assert not conductor.is_alive(), "the live run never ended"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    (copy,) = _run_dirs(home)
    assert _terminal_of(copy) is None, "the copy already has a terminal row"
    rows = records.lane_rows(copy)
    assert not rows.problems and not rows.torn, rows.problems
    classes = {row.cls for row in rows.rows}
    assert {"plan", "issue", "confirmation"} <= classes, classes
    _plan_bearing_tree(copy)
    _tidy(home)


def produce_trl_inflight_readiness_sibling(home: Path) -> None:
    """A non-terminal multi-vertex root: `readiness_sibling` copied while every leaf polls a
    readiness that never comes (the AllDeclaration crash image)."""
    _inflight(home, "readiness_sibling", INFLIGHT_LEAVES)


def produce_trl_views_exception_branch(home: Path) -> None:
    """The same finalized root as `trl-terminal-exception_branch`, whose expectation is its child
    views."""
    produce_trl_terminal_exception_branch(home)


def produce_trl_views_upstream_covered(home: Path) -> None:
    """The same finalized root as `trl-terminal-upstream_covered`, whose expectation is its child
    views."""
    produce_trl_terminal_upstream_covered(home)


# ---- the tree-tr5 band (L.TR-5.4): ChoiceNode roots


def produce_tr5_inflight_choice_long_running(home: Path) -> None:
    """A non-terminal ChoiceNode root: `choice_long_running` copied while the alternative the
    selection took (the declared fallback) is mid-step, its selection already recorded in the plan
    entry, then the live run is cancelled and reaped. The copy has no terminal row."""
    _inflight(home, "choice_long_running", CHOICE_INFLIGHT_LEAVES)


def produce_tr5_terminal_live_state(home: Path) -> None:
    """A finalized ChoiceNode-bearing root: `live_state` (`healthy`) selects a realization for each
    of its two choices and passes (the terminal row is the process state, `succeeded`)."""
    _finalized(home, "live_state", ("succeeded",), LIVE_STATE_ARGS)


def produce_tr5_views_live_state(home: Path) -> None:
    """The same finalized root as `tr5-terminal-live_state`, whose expectation is its child views
    (the alternatives the selection left out are `not_started` views)."""
    produce_tr5_terminal_live_state(home)
