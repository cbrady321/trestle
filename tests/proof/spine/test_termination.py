"""L.SV-5.13: J-SV5 termination over the real `loop.run_tree` step function (S-8's flip condition).

L.SV-5.17's model checks `join` and `decide` against a model of the attempt measure; this suite
drives the real thing. Each run is a real `Loop` (`trestle.workflow.loop`) over a real attempt lane
and a manual model clock (`tests/single/workflow/loopkit`): the loop-owned attempt measure in
`loop.py` (an ADVANCE that leaves the record unchanged ends the node) is what is proven, not a
transcription of it. The units and ports are fakes; an adversary chooses, per run, what `observe`
says on each call (a script of observation shapes) and what `advance` does (issue its effect and
have the port confirm it any of the ways a port can, decline, block, fail, raise). Every run must
return, write exactly one `NodeEnd`, and stay within the plan's measure:

* CONVERGE iterations (one `decide` call each): max_attempts x ceil(max_wait / poll_every) waiting
  polls, plus the polls a STALE node spends to the slice end, plus one ADVANCE-only iteration per
  attempt, one NoAction wait and the first and last joins (`Params.bound`, shared with the model);
* release-bound steps: per owned handle, the release call and the polls until observed absent or
  `release_timeout` (B1-C10 RELEASE_HANDLES).

A path past the cap is reported as `Unbounded`: S-8's flip condition, escalate, never patch. The
sweep also collects the (flags, condition, goal) rows `decide` was asked; it must reach every
condition the model says the join produces for those flags (the SA-09 drift check reads
`INPUTS` and `reachable_conditions`). SL leaves widen `INPUTS` (L.SL-4.1, L.SL-5.1, L.SL-6.1).
"""

from __future__ import annotations

import importlib.util
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tests.single.workflow import loopkit as kit
from tests.single.workflow.loopkit import (
    MARKER_EFFECTS,
    RUN_EFFECT,
    Marker,
    Rig,
    Runner,
    RunPort,
    Unit,
    declaration,
    effect,
    marker_ports,
    marker_unit,
    observation,
)
from tests.single.workflow.termination_model import Params, Unbounded, explore
from trestle.workflow import codes, loop
from trestle.workflow.decide import Command, decide
from trestle.workflow.declarations import (
    CompletionSource,
    EffectFacetClass,
    HostScopeRef,
    LeafDeclaration,
    LoopFlags,
    Repeat,
)
from trestle.workflow.units import Acted, Blocked, Failed, NoAction
from trestle.workflow.values import (
    Confirmation,
    ConfirmationStatus,
    CurrencyFact,
    Goal,
    Observation,
    RecordedResult,
    Resend,
)

pytestmark = pytest.mark.spine

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "workflows"

# Declared bounds of the swept leaves, small so a path is short on the model clock. The slice end
# is the model's (`slice_end_s`); the deadline is far past it so the dispatch re-check never stops
# a run (a deadline that does not fit is a B1-E7 stop, checked in test_loop.py).
POLL_S = 2.0
MAX_WAIT_S = 4.0
MAX_ATTEMPTS = 2
SLICE_END_S = 8.0
DEADLINE_S = 300.0
RELEASE_TIMEOUT_S = 2.0

RETRYABLE = "marker.retry"
FATAL = "marker.fatal"

APPLIED, NOT_APPLIED, UNKNOWN = (
    ConfirmationStatus.APPLIED,
    ConfirmationStatus.NOT_APPLIED,
    ConfirmationStatus.UNKNOWN,
)


# ------------------------------------------------------------------------------ the inputs


@dataclass(frozen=True)
class OneVertexInput:
    """One swept leaf shape: its declaration's flags and bounds. `decl` builds the declaration."""

    id: str
    flags: LoopFlags
    decl: Callable[[], LeafDeclaration]
    poll_s: float
    max_wait_s: float
    max_attempts: int
    slice_end_s: float
    release_timeout_s: float = RELEASE_TIMEOUT_S

    @property
    def params(self) -> Params:
        return Params(
            completion=self.flags.completion,
            repeat=self.flags.repeat,
            max_attempts=self.max_attempts,
            poll=max(1, round(self.poll_s)),
            max_wait=max(1, round(self.max_wait_s)),
            slice_end=max(1, round(self.slice_end_s)),
            release_timeout=max(1, round(self.release_timeout_s)),
        )

    @property
    def bound(self) -> int:
        """`Params.bound` recomputed on the input's real (possibly fractional) bounds."""
        if self.flags.completion is CompletionSource.RECORDED:
            return self.max_attempts + 1
        waits = math.ceil(self.max_wait_s / self.poll_s)
        return (
            self.max_attempts * waits
            + math.ceil(self.slice_end_s / self.poll_s)
            + self.max_attempts
            + waits
            + 2
        )

    @property
    def release_cost(self) -> int:
        return 1 + math.ceil(self.release_timeout_s / self.poll_s)


def _effects(completion: CompletionSource) -> tuple[Any, ...]:
    if completion is CompletionSource.RECORDED:
        return (effect(RUN_EFFECT, EffectFacetClass.EVENT, release_timeout_s=None),)
    return MARKER_EFFECTS


def _swept(id_: str, completion: CompletionSource, repeat: Repeat) -> OneVertexInput:
    def build() -> LeafDeclaration:
        return declaration(
            completion=completion,
            repeat=repeat,
            effects=_effects(completion),
            preconditions=("pre0",),
            retryable=frozenset({RETRYABLE}),
            max_attempts=MAX_ATTEMPTS,
            poll_s=POLL_S,
            max_wait_s=MAX_WAIT_S,
        )

    return OneVertexInput(
        id_,
        build().flags,
        build,
        POLL_S,
        MAX_WAIT_S,
        MAX_ATTEMPTS,
        SLICE_END_S,
    )


def fixture_declaration(name: str) -> LeafDeclaration:
    """The `DECLARATION` a published fixture file declares (imported by path, never run)."""
    spec = importlib.util.spec_from_file_location(f"_termination_fixture_{name}", FIXTURES / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = module.DECLARATION
    assert isinstance(found, LeafDeclaration)
    return found


def _fixture_input(name: str) -> OneVertexInput:
    """A published fixture's own declaration, swept with the fixture's bounds and the marker
    adversary (the fixture's effects are the marker's `up` and `stop`)."""

    def build() -> LeafDeclaration:
        decl = fixture_declaration(name)
        # `env_key_field` is a lease-key declaration the harness admission needs a request for; the
        # loop never reads it, so the sweep declares none (the MCP path is test_w_a1.py's)
        return replace(
            decl,
            preconditions=("pre0",),
            retryable=frozenset({RETRYABLE}),
            env_key_field=None,
        )

    decl = build()
    poll = decl.wait.poll_every.total_seconds()
    max_wait = decl.wait.max_wait.total_seconds()
    return OneVertexInput(
        Path(name).stem,
        decl.flags,
        build,
        poll,
        max_wait,
        decl.max_attempts,
        # room for every attempt's whole wait, so the sweep can reach the bound's polling class
        decl.max_attempts * max_wait + 2 * poll,
    )


# The swept leaf shapes. SL leaves add theirs (transient_leaf, slow_converge_leaf, lagging_leaf,
# remedy_leaf: L.SL-4.1, L.SL-5.1, L.SL-6.1) with the bound extended by the remedy budget.
INPUTS: dict[str, OneVertexInput] = {
    "observed-safe": _swept("observed-safe", CompletionSource.OBSERVED, Repeat.SAFE),
    "observed-once": _swept("observed-once", CompletionSource.OBSERVED, Repeat.ONCE),
    "recorded-safe": _swept("recorded-safe", CompletionSource.RECORDED, Repeat.SAFE),
    "recorded-once": _swept("recorded-once", CompletionSource.RECORDED, Repeat.ONCE),
    "spine_leaf": _fixture_input("spine_leaf.py"),
}


# ------------------------------------------------------------------------------ the adversary

_DRIFT = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g0", None)
_OLDER = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g1", None, older=codes.CREDENTIAL_STALE)
_SHORT = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g1", kit.NOW + timedelta(seconds=1))


def _shape(**fields: Any) -> Observation:
    identity = fields.pop("identity", True)
    made = observation(pre=(fields.pop("pre", True),), **fields)
    return made if identity else replace(made, identity_proven=False)


OBSERVED_SHAPES: dict[str, Observation] = {
    "absent": _shape(),
    "absent-precondition-fails": _shape(pre=False),
    "could-not-observe": _shape(code="marker.cli_missing"),
    "selector-unready": _shape(selector_present=True),
    "selector-ready": _shape(selector_present=True, ready=True),
    "found-ready": _shape(found=1, ready=True),
    "found-unready": _shape(found=1),
    "found-unproven": _shape(found=1, ready=True, identity=False),
    "found-drift": _shape(found=1, ready=True, currency=(_DRIFT,)),
    "selector-drift": _shape(selector_present=True, currency=(_DRIFT,)),
    "selector-older": _shape(selector_present=True, ready=True, currency=(_OLDER,)),
    "found-older": _shape(found=1, ready=True, currency=(_OLDER,)),
    "selector-short-lifetime": _shape(selector_present=True, ready=True, currency=(_SHORT,)),
}
RECORDED_SHAPES: dict[str, Observation] = {
    "preconditions-hold": _shape(),
    "precondition-fails": _shape(pre=False),
}


def observation_scripts(completion: CompletionSource) -> dict[str, list[Observation]]:
    """What `observe` answers on its n-th call (the last answer repeats): each shape held, and
    each shape reached after the resource was absent twice (a resource that comes up later)."""
    shapes = RECORDED_SHAPES if completion is CompletionSource.RECORDED else OBSERVED_SHAPES
    scripts = {name: [shape] for name, shape in shapes.items()}
    if completion is CompletionSource.OBSERVED:
        first = shapes["absent"]
        for name, shape in shapes.items():
            if name != "absent":
                scripts[f"absent-then-{name}"] = [first, first, shape]
    return scripts


def _blocked(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
    return Blocked("unit.blocked", "do the thing", Resend.SUCCEEDS_AFTER_ACTION)


def _failed(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
    return Failed("unit.failed", "no")


def _no_action(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
    return NoAction("unit.nothing_to_do")


def _acts_without_effect(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
    return Acted()


def _raises(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
    raise RuntimeError("the unit died")


class _Recorded:
    """An event port's `HasRecordedResult` (V-5.5)."""

    def __init__(self, passed: bool) -> None:
        self.recorded = RecordedResult(passed, None if passed else "unit.test_failed", None)


class _ResultRunner(Runner):
    """An event port that confirms APPLIED and reports the recorded result of what it ran."""

    def __init__(self, passed: bool) -> None:
        super().__init__(Confirmation(APPLIED, None, None))
        self._passed = passed

    def run(self, name: str, ticket: Any) -> Any:
        self.calls += 1
        return Confirmation(APPLIED, None, None), _Recorded(self._passed)


class _BoomRunner(Runner):
    def run(self, name: str, ticket: Any) -> Any:
        self.calls += 1
        raise RuntimeError("the port died")


def _run_advance(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
    """Starts the event through its ticketed facet; a port that dies after its ticket is durable
    is caught the way a careful unit does (the facet already recorded UNKNOWN)."""
    try:
        effects.event(RunPort).run("t", RUN_EFFECT)
    except RuntimeError as exc:
        return Failed("unit.caught", str(exc))
    return Acted()


# what the port confirms on an issued effect: (name, marker/runner factory input)
MARKER_OUTCOMES: dict[str, tuple[ConfirmationStatus, str | None]] = {
    "applied": (APPLIED, None),
    "not-applied-retryable": (NOT_APPLIED, RETRYABLE),
    "not-applied-fatal": (NOT_APPLIED, FATAL),
    "not-applied-interactive": (NOT_APPLIED, codes.CREDENTIAL_INTERACTIVE),
    "unknown": (UNKNOWN, None),
}
# ways the unit's own advance can end without (or besides) a port outcome
UNIT_ADVANCES: dict[str, Callable[..., Any]] = {
    "blocked": _blocked,
    "failed": _failed,
    "no-action": _no_action,
    "acts-without-effect": _acts_without_effect,
    "raises": _raises,
}


def advance_scenarios(completion: CompletionSource) -> list[str]:
    names = [f"port-{name}" for name in MARKER_OUTCOMES]
    if completion is CompletionSource.RECORDED:
        names += ["port-raises", "port-applied-passed", "port-applied-failed"]
    return names + list(UNIT_ADVANCES)


@dataclass
class Run:
    rig: Rig
    unit: Unit
    decides: list[tuple[LoopFlags, Any, Goal]] = field(default_factory=list)


def _build_run(
    inp: OneVertexInput,
    tmp_path: Path,
    script: list[Observation],
    scenario: str,
) -> Run:
    decl = inp.decl()
    recorded = inp.flags.completion is CompletionSource.RECORDED
    seen = 0

    def observe(unit: Unit, params: Any, reads: Any, ctx: Any) -> Observation:
        nonlocal seen
        answer = script[min(seen, len(script) - 1)]
        seen += 1
        return answer

    port_ports: dict[type, object]
    if scenario.startswith("port-"):
        name = scenario.removeprefix("port-")
        if recorded and name == "raises":
            runner: Runner = _BoomRunner()
        elif recorded and name.startswith("applied-"):
            runner = _ResultRunner(name == "applied-passed")
        elif recorded:
            status, code = MARKER_OUTCOMES[name]
            runner = Runner(Confirmation(status, code, None))
        else:
            runner = Runner()
        if recorded:
            port_ports = {RunPort: runner}
            unit = Unit(decl, observe, _run_advance)
        else:
            status, code = MARKER_OUTCOMES[name]
            marker = Marker(create_status=status, create_code=code)
            port_ports = marker_ports(marker)
            base = marker_unit(marker, decl)
            unit = Unit(decl, observe, base._advance, base._release)
    else:
        port_ports = {}
        marker = Marker()
        base = marker_unit(marker, decl)
        unit = Unit(decl, observe, UNIT_ADVANCES[scenario], base._release)
        if not recorded:
            port_ports = marker_ports(marker)
    rig = kit.build(
        tmp_path,
        unit,
        ports=port_ports,
        deadline_s=DEADLINE_S,
        slice_end_s=inp.slice_end_s,
    )
    return Run(rig, unit)


def drive(run: Run, cap: int, table: Callable[..., tuple[Command, ...]] = decide) -> int:
    """Run the real loop once; the CONVERGE `decide` calls it made. `table` is the decision table
    the loop consults (a planted one in the self-tests). A call past `cap` raises `Unbounded`:
    no progress, S-8's flip condition."""
    real = table
    converge = 0

    def counted(flags: LoopFlags, condition: Any, goal: Goal) -> tuple[Command, ...]:
        nonlocal converge
        run.decides.append((flags, condition, goal))
        if goal is Goal.CONVERGE:
            converge += 1
            if converge > cap:
                raise Unbounded(f"more than {cap} CONVERGE iterations: no progress")
        return real(flags, condition, goal)

    with mock.patch.object(loop, "decide", counted):
        run.rig.run()
    return converge


# ------------------------------------------------------------------------------ the sweep


@dataclass
class Sweep:
    runs: int = 0
    max_iterations: int = 0
    max_polls: int = 0
    max_release_steps: int = 0
    conditions: set[str] = field(default_factory=set)
    rows: set[tuple[str, str, str, str, str]] = field(default_factory=set)
    ends: int = 0


def sweep_input(inp: OneVertexInput, tmp_path: Path) -> Sweep:
    """Drive the real loop for every (observation script x advance scenario) of `inp`."""
    out = Sweep()
    cap = inp.bound * 2 + 10
    for s_name, script in observation_scripts(inp.flags.completion).items():
        for scenario in advance_scenarios(inp.flags.completion):
            out.runs += 1
            where = tmp_path / f"run{out.runs:04d}"
            where.mkdir()
            run = _build_run(inp, where, script, scenario)
            label = f"{inp.id}/{s_name}/{scenario}"
            try:
                iterations = drive(run, cap)
            except Unbounded as exc:
                raise Unbounded(f"{label}: {exc}") from exc
            # every run ends: exactly one NodeEnd per vertex (B1-C11)
            assert len(run.rig.ends()) == 1, label
            out.ends += 1
            polls = sum(1 for w in run.rig.cancel.waits if w == timedelta(seconds=inp.poll_s))
            # tickets of the node's own effects; the release pass's `stop` tickets are its own
            handles = sum(1 for t in run.rig.rows("issue") if t["effect"] != kit.STOP_EFFECT)
            release_steps = run.unit.releases + len(run.rig.sleeps)
            assert iterations <= inp.bound, (label, iterations, inp.bound)
            assert polls <= inp.max_attempts * math.ceil(inp.max_wait_s / inp.poll_s) + math.ceil(
                inp.slice_end_s / inp.poll_s
            ), (label, polls)
            assert handles <= inp.max_attempts, (label, handles)  # never past max_attempts
            assert release_steps <= inp.max_attempts * inp.release_cost, (label, release_steps)
            out.max_iterations = max(out.max_iterations, iterations)
            out.max_polls = max(out.max_polls, polls)
            out.max_release_steps = max(out.max_release_steps, release_steps)
            for flags, condition, goal in run.decides:
                out.rows.add(
                    (
                        flags.compose.value,
                        flags.completion.value,
                        flags.repeat.value,
                        getattr(condition, "value", str(condition)),
                        goal.value,
                    )
                )
                if goal is Goal.CONVERGE:
                    out.conditions.add(getattr(condition, "value", str(condition)))
    return out


@cache
def reachable_conditions(flags: LoopFlags) -> frozenset[str]:
    """The conditions the join produces for `flags` at one vertex, by the exhaustive model
    (L.SV-5.17): the rows the real sweep must reach. `unsatisfied` is the start of every walk."""
    report = explore(Params(flags.completion, flags.repeat))
    return frozenset(report.conditions) | {"unsatisfied"}


@pytest.mark.parametrize("input_id", list(INPUTS))
def test_run_tree_terminates_over_every_one_vertex_input(input_id: str, tmp_path: Path) -> None:
    """Every path of the real `run_tree` step function returns within max_attempts x
    ceil(max_wait / poll_every) + the release-bound steps: the RECORDED ADVANCE, REJOIN path
    included, the loop-owned attempt measure included. An unbounded path raises `Unbounded`
    (S-8's flip condition, escalate, never patch)."""
    inp = INPUTS[input_id]
    swept = sweep_input(inp, tmp_path)
    # not vacuous: real paths of every length class ran, every condition the join produces was
    # asked of `decide` under CONVERGE, and the release pass ran
    assert swept.runs >= 2 * len(advance_scenarios(inp.flags.completion))
    assert swept.ends == swept.runs
    assert swept.max_iterations >= 3
    missing = reachable_conditions(inp.flags) - swept.conditions
    assert missing == set(), (input_id, missing)
    assert any(row[4] == Goal.RELEASE.value for row in swept.rows)
    if inp.flags.completion is CompletionSource.OBSERVED:
        assert swept.max_polls >= 1
        # a resource that never turns ready spends the whole wait, so the sweep reaches the bound's
        # polling class, not only the short paths
        assert swept.max_polls >= inp.max_attempts * math.ceil(inp.max_wait_s / inp.poll_s) - 1


# ------------------------------------------------------------------------------ planted defects


def _planted_run(tmp_path: Path, inp: OneVertexInput) -> Run:
    scripts = observation_scripts(inp.flags.completion)
    return _build_run(inp, tmp_path, scripts["absent"], "port-applied")


def test_planted_unbounded_table_row_is_reported(tmp_path: Path) -> None:
    """S-8's flip condition on the real loop: a table whose UNSATISFIED row neither advances nor
    waits re-joins the same record forever, and the drive reports it unbounded."""
    inp = INPUTS["observed-safe"]
    real = decide

    def planted(flags: LoopFlags, condition: Any, goal: Goal) -> tuple[Command, ...]:
        if goal is Goal.CONVERGE and getattr(condition, "value", None) == "unsatisfied":
            return (Command.REJOIN,)
        return real(flags, condition, goal)

    run = _planted_run(tmp_path, inp)
    with pytest.raises(Unbounded):
        drive(run, inp.bound * 2 + 10, planted)


def test_planted_readvance_row_is_ended_by_the_loop_owned_measure(tmp_path: Path) -> None:
    """A CONVERGING row that re-advances (ADVANCE, REJOIN) instead of waiting spends its attempts,
    is then refused ATTEMPTS_SPENT (which records nothing), and the loop's own attempt measure ends
    the node UNIT_RAISED instead of spinning: the drive returns, within the bound."""
    inp = INPUTS["observed-safe"]
    real = decide

    def planted(flags: LoopFlags, condition: Any, goal: Goal) -> tuple[Command, ...]:
        if goal is Goal.CONVERGE and getattr(condition, "value", None) == "converging":
            return (Command.ADVANCE, Command.REJOIN)
        return real(flags, condition, goal)

    scripts = observation_scripts(inp.flags.completion)
    run = _build_run(inp, tmp_path, scripts["absent"], "port-applied")
    iterations = drive(run, inp.bound * 2 + 10, planted)
    assert iterations <= inp.bound
    (end,) = run.rig.ends()
    assert end["code"] == codes.UNIT_RAISED and end["condition"] == "failed", end
    # the unplanted table waits through the same record instead, and ends on the wait's timeout
    (tmp_path / "calm").mkdir()
    calm = _build_run(inp, tmp_path / "calm", scripts["absent"], "port-applied")
    assert drive(calm, inp.bound * 2 + 10) <= inp.bound
    assert calm.rig.ends()[0]["code"] == codes.POSTCONDITION_TIMEOUT


def test_loop_owned_attempt_measure_ends_a_node_whose_advance_changes_nothing(
    tmp_path: Path,
) -> None:
    """The measure is the loop's, not the unit's: an ADVANCE that issues no ticket and records
    nothing ends the node UNIT_RAISED after one call (loop.py, B1-E5), never a second."""
    inp = INPUTS["observed-safe"]
    scripts = observation_scripts(inp.flags.completion)
    run = _build_run(inp, tmp_path, scripts["absent"], "acts-without-effect")
    drive(run, inp.bound * 2 + 10)
    assert run.unit.advances == 1
    (end,) = run.rig.ends()
    assert end["code"] == codes.UNIT_RAISED


def test_bound_check_is_not_vacuous(tmp_path: Path) -> None:
    """The bound is tight enough to fail: shrinking it below what a hanging resource spends is
    caught by the sweep's own assertion, so a loose sweep cannot pass by accident."""
    inp = INPUTS["observed-safe"]
    scripts = observation_scripts(inp.flags.completion)
    run = _build_run(inp, tmp_path, scripts["absent"], "port-applied")
    iterations = drive(run, inp.bound * 2 + 10)
    assert iterations > 3
    assert iterations <= inp.bound
    assert iterations > inp.bound // 4
