"""L.SL-5.1: polling on the declared wait policy (B1-C10, V-14, V-3.4, V-3.5, B1-I3; WR-VERIFY-1
`postcondition-after-last-action`).

Under goal CONVERGE `POLL` waits the wait policy's next interval through `CancelSignal.wait`, then
observes and joins; it never calls `advance`. The unit is the published fixture
`tests/fixtures/workflows/slow_converge_leaf.py` over the fake marker (a real child process the
release pass stops), under the loop tests' manual clock (`loopkit`): the clock only moves when the
loop waits, so every interval is read exactly and nothing sleeps."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker

from tests.single.workflow import loopkit as kit
from tests.single.workflow.loopkit import (
    Marker,
    Rig,
    Unit,
    declaration,
    marker_ports,
    marker_unit,
    observation,
)
from trestle.workflow import codes, loop, ports
from trestle.workflow.units import Blocked
from trestle.workflow.values import Resend

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "workflows" / "slow_converge_leaf.py"
LOOP_SOURCE = Path(loop.__file__)


def _fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("slow_converge_leaf_fixture", FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _slow_rig(
    tmp_path: Path,
    *,
    lag: int = 3,
    never: bool = False,
    poll_every_s: float = 1.0,
    backoff: float = 2.0,
    max_wait_s: float = 60.0,
    slice_end_s: float | None = None,
) -> tuple[Rig, Any, FakeMarker]:
    fixture = _fixture()
    unit = fixture.make_unit(
        fixture.declaration(
            poll_every_s=poll_every_s,
            backoff=backoff,
            max_wait_s=max_wait_s,
            env_key_field=None,
        )
    )
    marker = FakeMarker(tmp_path / "markers", "run", lag_polls=lag, never_ready=never)
    rig = kit.build(
        tmp_path,
        unit,
        ports={
            ports.ResourceReads: marker,
            ports.ResourceCreate: marker,
            ports.ResourceOwned: marker,
        },
        slice_end_s=slice_end_s,
    )
    return rig, unit, marker


def _seconds(rig: Rig) -> list[float]:
    return [wait.total_seconds() for wait in rig.cancel.waits]


@pytest.mark.proves(
    "WR-VERIFY-1", "WR-VERIFY-1:postcondition-after-last-action", "A", "single", "LOGIC+PROC", "CI"
)
def test_polled_not_reinvoked(tmp_path: Path) -> None:
    rig, unit, _ = _slow_rig(tmp_path, lag=4, backoff=1.0)
    rig.run()
    assert unit.advances == 1, "advance is called once and never again just to wait"
    assert len(rig.cancel.waits) >= 4, "the wait is a poll, not a re-advance"
    assert unit.observes >= 1 + len(rig.cancel.waits), "one observe at the start, one per poll"
    assert rig.classes().count("issue") == 2, "one create and, in the release pass, one stop"
    (end,) = rig.ends()
    assert (end["condition"], end["provenance"]) == ("satisfied", "created")


def test_intervals_follow_wait_policy_backoff(tmp_path: Path) -> None:
    rig, _, _ = _slow_rig(tmp_path, lag=4, poll_every_s=1.0, backoff=2.0)
    rig.run()
    waited = _seconds(rig)
    assert waited == [1.0, 2.0, 4.0, 8.0, 16.0][: len(waited)] and len(waited) >= 4, waited
    flat, _, _ = _slow_rig(tmp_path / "flat", lag=3, poll_every_s=0.5, backoff=1.0)
    flat.run()
    assert set(_seconds(flat)) == {0.5}, "backoff 1.0 keeps the declared interval"


def test_interval_never_passes_the_slice_end(tmp_path: Path) -> None:
    rig, unit, _ = _slow_rig(
        tmp_path, never=True, poll_every_s=1.0, backoff=4.0, max_wait_s=100.0, slice_end_s=6.0
    )
    rig.run()
    assert _seconds(rig) == [1.0, 4.0, 1.0], "the third wait is cut to the 1 s the slice has left"
    (end,) = rig.ends()
    assert end["code"] == codes.CARVE_EXCEEDED, "a node is polled at most until its slice ends"
    assert unit.advances == 1


def test_elapsed_max_wait_is_postcondition_timeout(tmp_path: Path) -> None:
    rig, unit, _ = _slow_rig(tmp_path, never=True, poll_every_s=1.0, backoff=2.0, max_wait_s=5.0)
    rig.run()
    assert _seconds(rig) == [1.0, 2.0, 4.0], "polled until the summed waits reach max_wait"
    (end,) = rig.ends()
    assert (end["condition"], end["code"], end["cut"]) == (
        "failed",
        codes.POSTCONDITION_TIMEOUT,
        None,
    )
    assert end["provenance"] == "created"
    assert unit.advances == 1, "no remedy is declared, so the timeout is not retried"
    assert rig.rows("released"), "the created marker is still released on the way out"


def test_prior_run_precondition_never_counts(tmp_path: Path) -> None:
    """A precondition observed true by an earlier root run is not read by the next one (V-3.4):
    the second root run observes it false and stops BLOCKED before any effect."""
    granted = {"value": True}

    def observe(unit: Unit, params: Any, reads: Any, ctx: Any) -> Any:
        return observation(preconditions=("auth",), pre=(granted["value"],), ready=False)

    def advance(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        return Blocked("unit.needs", "Do the thing.", Resend.SUCCEEDS_AFTER_ACTION)

    decl = declaration(preconditions=("auth",))
    first = Unit(decl, observe, advance)
    rig_a = kit.build(tmp_path / "a", first)
    rig_a.run()
    (end_a,) = rig_a.ends()
    assert first.advances == 1 and end_a["code"] != codes.PRECONDITION_UNSATISFIED

    granted["value"] = False  # revoked between the two root runs
    second = Unit(decl, observe, advance)
    rig_b = kit.build(tmp_path / "b", second)
    rig_b.run()
    (end_b,) = rig_b.ends()
    assert (end_b["condition"], end_b["code"]) == ("blocked", codes.PRECONDITION_UNSATISFIED)
    assert second.advances == 0, "the earlier run's true observation bought no advance"
    assert rig_b.classes() == ["plan", "end"], "nothing was issued"


class _PhaseMarker(Marker):
    """A marker that logs which phase of the walk each write happened in, and whether the
    ticket's `issue` entry was already durable when it did."""

    def __init__(self, rig_holder: list[Rig], phase: list[str]) -> None:
        super().__init__(ready_after=2)
        self._rigs = rig_holder
        self.phase = phase
        self.writes: list[tuple[str, str, int]] = []  # (call, phase, issue rows durable then)

    def _log(self, call: str) -> None:
        durable = sum(1 for cls in self._rigs[0].classes() if cls == "issue")
        self.writes.append((call, self.phase[0], durable))

    def create(self, spec: Any, ticket: Any) -> Any:
        self._log("create")
        return super().create(spec, ticket)

    def stop(self, target: Any, ticket: Any) -> Any:
        self._log("stop")
        return super().stop(target, ticket)


def test_action_invocation_only_via_facets(tmp_path: Path) -> None:
    phase = ["loop"]
    holder: list[Rig] = []
    marker = _PhaseMarker(holder, phase)
    inner = marker_unit(marker)

    def observe(unit: Unit, params: Any, reads: Any, ctx: Any) -> Any:
        phase[0] = "observe"
        try:
            return inner.observe(params, reads, ctx)
        finally:
            phase[0] = "loop"

    def advance(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        phase[0] = "advance"
        try:
            return inner.advance(params, state, effects, ctx)
        finally:
            phase[0] = "loop"

    def release(unit: Unit, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        phase[0] = "release"
        try:
            return inner.release(params, handle, effects, ctx)
        finally:
            phase[0] = "loop"

    unit = Unit(inner.decl, observe, advance, release)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    holder.append(rig)
    rig.run()
    assert rig.cancel.waits, "the run polled"
    assert [(call, where) for call, where, _ in marker.writes] == [
        ("create", "advance"),
        ("stop", "release"),
    ], "a port write happens only inside advance or release"
    assert [issued for _, _, issued in marker.writes] == [1, 2], "each ticket was durable first"
    assert unit.advances == 1


def test_loop_source_calls_no_port_write() -> None:
    """The loop never reaches a port write itself: every port call it makes is a read facet
    (`observe`), the writes belong to the facets the unit is handed (B1-I3)."""
    tree = ast.parse(LOOP_SOURCE.read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & {"create", "stop", "restart", "recreate", "submit"}, called
