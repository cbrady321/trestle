"""The product's direct/child runs for `differ d6` (L.TR-6.1; MC-06 mode d6 is the judge in
`tests/proof/differ_modes/d6_direct_child.py`).

`pairs()` is d6's default pair source: each pair runs one unit of `root_eligible_both` (`work`,
the unit both OQ-31 variants accept as a root entry, L.TR-0.1) twice through the real loop
in-library (MC-26's rig: the real lane and services under a manual clock), with the same params and
the same machine state:

* directly, as a one-vertex root;
* inside the fixture's two-level parent (`both`: `prep`, then `work` needing `prep`).

The machine state is one fake port whose markers are keyed by the *unit's name*, never by its
position, so a unit meets the same state wherever it runs (V-8 L-5's lineage-derived selectors are
the one thing that would otherwise differ, and d6 compares only whether a selector is present).
`prep` and `work` are scripted leaves over the fixture's own declarations (`tk.rig_of_entry`).

The scenarios (`SCENARIOS`) are the four WR-UNIT-1 behaviours d6 pins and the after-stop variant:

* `fresh`: nothing present; the unit creates what it needs and releases it (equiv-verdict);
* `satisfied`: its postcondition already holds; it records no effect (direct-skip-satisfied);
* `foreign`: a resource it did not create is present; only its own is released
  (direct-releases-own-only);
* `once`: a ONCE effect is issued once and never again (once-not-reissued);
* `after_stop`: a sibling raises while the unit is mid-wait: the pair is compared up to the parent's
  first whole-root stop record (SV-7a).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tests.proof import tolerances
from tests.proof.differ_modes import d6_direct_child as d6
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.server import answer
from trestle.workflow.declarations import Repeat, WorkflowEntry
from trestle.workflow.units import ActContext, EffectFacets, Step
from trestle.workflow.values import (
    Confirmation,
    ConfirmationStatus,
    RecordedResult,
    Verdict,
)

FIXTURE = "root_eligible_both"
NODE = "work"
RAISED = "loop_unit_raised"  # the evidence the loop emits right after it flipped the root's goal

SCENARIOS = ("fresh", "satisfied", "foreign", "once", "after_stop")


class UnitMarker(tk.PathMarker):
    """`tk.PathMarker` whose markers are keyed by the unit's own name (the last segment of its
    lineage path; the root's name when the path is empty): one machine state wherever it runs."""

    def __init__(self, root: str) -> None:
        super().__init__()
        self._root = root

    def _text(self, lineage: Any) -> str:  # type: ignore[override]
        segments = lineage.path.segments
        return str(segments[-1]) if segments else self._root

    def plant(self, key: str) -> None:
        with self._lock:
            self._live.add(key)

    def live(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._live)


class UnitEvents(tk.EventPort):
    """`tk.EventPort` answering per unit (by name, whatever the position)."""

    def __init__(
        self,
        root: str,
        by_unit: dict[str, tuple[ConfirmationStatus, RecordedResult | None, str | None]],
    ) -> None:
        super().__init__()
        self._root = root
        self._by_unit = by_unit

    def run(self, name: str, ticket: Any) -> Any:
        segments = ticket.lineage.path.segments
        unit = str(segments[-1]) if segments else self._root
        with self._lock:
            self.calls.append(unit)
        status, result, code = self._by_unit[unit]
        return Confirmation(status, code, None), None if result is None else tk.Recorded(result)


@dataclass
class Run:
    """One finished run: the rig, and what d6 reads of it."""

    rig: tk.TreeRig
    marker: UnitMarker | None = None
    events: UnitEvents | None = None

    def position(self, path: str) -> d6.Position:
        entries = self.rig.rows()
        wire = answer.to_wire_full(tk.answer_of(self.rig))
        return d6.Position.of(entries, wire, path, stop_seq=d6.first_exception_stop(entries))


def entries(directory: Path) -> tuple[WorkflowEntry, WorkflowEntry]:
    """The fixture's parent tree and `work` alone as a root (the same declaration)."""
    parent = tk.fixture_entry(FIXTURE)
    direct = replace(parent, root=NODE, units={NODE: parent.units[NODE]})
    return direct, parent


def _run(rig: tk.TreeRig, marker: UnitMarker | None, events: UnitEvents | None = None) -> Run:
    rig.run()
    return Run(rig, marker, events)


def plain(directory: Path, *, plant: tuple[str, ...] = ()) -> tuple[Run, Run]:
    """`work` created from nothing (or over `plant`ed markers) directly and in the parent."""
    direct_entry, parent_entry = entries(directory)
    runs: list[Run] = []
    for name, entry in (("direct", direct_entry), ("child", parent_entry)):
        marker = UnitMarker(NODE)
        for key in plant:
            marker.plant(key)
        rig = tk.rig_of_entry(directory / name, entry, marker)
        runs.append(_run(rig, marker))
    return runs[0], runs[1]


def once(directory: Path) -> tuple[Run, Run]:
    """`work` a RECORDED ONCE effect whose one attempt applied and failed: never issued again."""
    direct_entry, parent_entry = entries(directory)
    applied_failed = (
        ConfirmationStatus.APPLIED,
        RecordedResult(False, "unit.tests_failed", None),
        None,
    )
    applied_passed = (ConfirmationStatus.APPLIED, RecordedResult(True, None, None), None)
    runs: list[Run] = []
    for name, entry in (("direct", direct_entry), ("child", parent_entry)):
        events = UnitEvents(NODE, {NODE: applied_failed, "prep": applied_passed})
        behaviour = {
            NODE: tk.recorded_unit(NODE, repeat=Repeat.ONCE),
            "prep": tk.recorded_unit("prep"),
        }
        rig = tk.rig_of_entry(
            directory / name,
            entry,
            behaviour={k: v for k, v in behaviour.items() if k in entry.units},
            port_impl={kit.RunPort: events},
        )
        runs.append(_run(rig, None, events))
    return runs[0], runs[1]


def _poll(predicate: Callable[[], bool]) -> bool:
    end = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(tolerances.POLL_S)
    return predicate()


def after_stop(directory: Path) -> tuple[Run, Run]:
    """`work` mid-wait when a sibling (`boom`) raises: alone (gate open, it runs its course) and in
    the parent (`app`: `boom` and `work` at once). The gate holds `work` inside its wait until the
    loop has flipped the goal, so the stop reaches it mid-wait every time."""
    # direct: the same unit, never stopped
    gate = threading.Event()
    gate.set()
    marker = UnitMarker(NODE)
    rig = tk.tree_rig(directory / "direct", tk.held_unit(NODE, gate), {}, marker)
    direct = _run(rig, marker)

    gate = threading.Event()
    held = threading.Event()
    marker = UnitMarker(NODE)

    def raises(
        unit: Any, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext
    ) -> Step:
        assert held.wait(tolerances.JOIN_WAIT_S), "work never reached its wait"
        raise RuntimeError("d6: the sibling raised")

    app = tk.group("app", (tk.bind("boom"), tk.bind(NODE)), concurrency=2, budget_s=100)
    child_rig = tk.tree_rig(
        directory / "child",
        app,
        {
            "boom": tk.leaf_unit("boom", advance=raises),
            NODE: tk.held_unit(NODE, gate, on_hold=lambda path: held.set()),
        },
        marker,
    )

    def open_gate_once_raised() -> None:
        _poll(lambda: RAISED in child_rig.rig.sink.kinds())
        gate.set()

    opener = threading.Thread(target=open_gate_once_raised)
    opener.start()
    try:
        child = _run(child_rig, marker)
    finally:
        gate.set()
        opener.join(tolerances.JOIN_WAIT_S)
    return direct, child


def scenario(name: str, directory: Path) -> tuple[Run, Run]:
    if name == "fresh":
        return plain(directory)
    if name == "satisfied":
        return plain(directory, plant=(NODE,))
    if name == "foreign":
        return plain(directory, plant=("other",))
    if name == "once":
        return once(directory)
    if name == "after_stop":
        return after_stop(directory)
    raise KeyError(name)


def pair_of(name: str, directory: Path) -> d6.Pair:
    direct, child = scenario(name, directory)
    return d6.Pair(name, direct.position(""), child.position(NODE))


def pairs() -> list[d6.Pair]:
    """d6's default pair source: every scenario, each in its own scratch directory."""
    import tempfile

    out: list[d6.Pair] = []
    with tempfile.TemporaryDirectory(prefix="d6-pairs-") as scratch:
        for name in SCENARIOS:
            out.append(pair_of(name, Path(scratch) / name))
    return out
