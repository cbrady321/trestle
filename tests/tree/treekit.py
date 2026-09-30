"""Helpers for the TR-3 loop cases: a fixture made runnable, and the lane read back (L.TR-3.1).

A structural fixture (MC-B3-01) is declaration only and its function returns at once, so no
fixture runs a tree by itself. `runnable_source` rewrites the one entry function of a fixture (or
a generated tree) to make the loop's one call, `run_tree(ctx, ENTRY, {})` (B1-C9), which is the
only difference between the published source and the source a run executes."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from tests.proof import records, tolerances
from tests.single.workflow import loopkit as kit
from trestle.child import run_services as rs
from trestle.common.plan import carving, compiler
from trestle.workflow import ports
from trestle.workflow.declarations import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectFacetClass,
    LeafDeclaration,
    LoopFlags,
    Repeat,
    WorkflowEntry,
)
from trestle.workflow.extract import extract_root
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import (
    CheckResult,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    Lineage,
    Observation,
    RecordedResult,
    SelectorRef,
    Verdict,
)

REPO = Path(__file__).resolve().parents[2]
TREES = REPO / "tests" / "fixtures" / "trees"

_RETURN = re.compile(r"^    return \{.*\}\n\Z", re.MULTILINE)


def runnable_source(source: str) -> str:
    """`source` with its entry function's body replaced by the loop's one call."""
    body, count = _RETURN.subn("    run_tree(ctx, ENTRY, {})\n", source)
    assert count == 1, "the fixture's entry function is not the expected one-line return"
    anchor = "from trestle.plugin import"
    assert anchor in body
    return body.replace(anchor, "from trestle.workflow.loop import run_tree\n" + anchor, 1)


def fixture_source(name: str) -> str:
    return (TREES / f"{name}.py").read_text(encoding="utf-8")


def ends_by_path(run_dir: Path) -> dict[str, dict[str, object]]:
    """Every `NodeEnd` of the run's lane, keyed by the path text (`""` is the root)."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    ends: dict[str, dict[str, object]] = {}
    for row in lane.rows:
        if row.cls == "end":
            assert row.path is not None and row.path not in ends, row.path
            ends[row.path] = row.entry
    return ends


# ---- the in-library tree rig (L.TR-3.2 onward; MC-26's real lane and services, a manual clock)

EFFECT = kit.EFFECT
LEAF_BUDGET_S = 20.0
TREE_DEADLINE_S = 600.0


def bind(unit: str, *needs: str, name: str | None = None) -> ChildBinding:
    return ChildBinding(unit=unit, params={}, needs=tuple(needs), name=name)


def group(
    name: str, children: tuple[ChildBinding, ...], *, concurrency: int = 2, budget_s: float = 300.0
) -> AllDeclaration:
    return AllDeclaration(
        unit=name,
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=children,
        concurrency=concurrency,
        budget=timedelta(seconds=budget_s),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=None,
    )


class PathMarker:
    """`ResourceReads` / `ResourceCreate` / `ResourceOwned` for a tree: one in-memory marker per
    node, keyed by the node's lineage path (the loop hands every leaf the same port object, so the
    node is told apart by the lineage each call carries). Thread safe. `on_create(path)` runs
    inside the create call, after its ticket was issued: a test blocks or counts there."""

    def __init__(self, on_create: Callable[[str], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._live: set[str] = set()
        self.calls: list[tuple[str, str]] = []  # (verb, path text), in call order
        self.on_create = on_create
        self.descriptor = ports.InRunGroup()

    @staticmethod
    def _text(lineage: Lineage) -> str:
        return "/".join(lineage.path.segments)

    def observe(
        self, spec: ports.ResourceSpec, lineage: Lineage, effect: str | None
    ) -> ports.ResourceObservation:
        text = self._text(lineage)
        with self._lock:
            present = text in self._live
            self.calls.append(("observe", text))
        ref = SelectorRef(lineage, effect or EFFECT, f"sel-{text}", kit.NOW) if present else None
        return ports.ResourceObservation(present, ref, True, True, (), (), None)

    def check(self, check: str, target: Any) -> CheckResult:
        return CheckResult(True, None, "")

    def endpoint(self, target: Any, vantage: Any) -> Any:
        raise NotImplementedError

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        return self.descriptor

    def launch_policy(self, spec: ports.ResourceSpec) -> ports.ExecutionPolicy:
        raise NotImplementedError

    def create(self, spec: ports.ResourceSpec, ticket: Any) -> Confirmation:
        text = self._text(ticket.lineage)
        with self._lock:
            self.calls.append(("create", text))
        if self.on_create is not None:
            self.on_create(text)
        with self._lock:
            self._live.add(text)
        return Confirmation(ConfirmationStatus.APPLIED, None, f"sel-{text}")

    def stop(self, target: CreatedHandle, ticket: Any) -> Confirmation:
        text = self._text(ticket.lineage)
        with self._lock:
            self.calls.append(("stop", text))
            self._live.discard(text)
        return Confirmation(ConfirmationStatus.APPLIED, None, None)

    def restart(self, target: Any, ticket: Any) -> Confirmation:
        raise NotImplementedError

    def recreate(self, target: Any, ticket: Any) -> Confirmation:
        raise NotImplementedError

    def paths(self, verb: str) -> list[str]:
        return [path for v, path in self.calls if v == verb]


def port_map(marker: PathMarker) -> dict[type, object]:
    return {ports.ResourceReads: marker, ports.ResourceCreate: marker, ports.ResourceOwned: marker}


def leaf_unit(
    name: str,
    *,
    advance: Callable[[Any, Any, Verdict, EffectFacets, ActContext], Step] | None = None,
    budget_s: float = LEAF_BUDGET_S,
    max_attempts: int = 1,
    declaration: LeafDeclaration | None = None,
) -> kit.Unit:
    """A leaf named `name`: `observe` reads the node's marker (ready once present) and reports each
    declared precondition satisfied, `advance` creates the marker (or is `advance`), release stops
    it. One attempt unless told otherwise. `declaration` replaces the default one (a fixture's own
    flags, checks and budget), given the marker effects."""
    decl = (
        replace(declaration, effects=kit.MARKER_EFFECTS)
        if declaration is not None
        else replace(
            kit.declaration(
                effects=kit.MARKER_EFFECTS,
                max_attempts=max_attempts,
                budget_s=budget_s,
                max_wait_s=5.0,
            ),
            unit=name,
        )
    )

    def observe(unit: kit.Unit, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ports.ResourceReads)
        seen = resource.observe(kit.SPEC, ctx.lineage, EFFECT)
        ready = (
            resource.check("ready", seen.selector_ref) if seen.selector_ref is not None else None
        )
        return kit.observation(
            selector_present=seen.selector_present,
            ready=ready is not None and ready.satisfied,
            pre=tuple(True for _ in unit.decl.preconditions),
            preconditions=tuple(unit.decl.preconditions),
        )

    def create(
        unit: kit.Unit, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext
    ) -> Step:
        effects.create(ports.ResourceCreate).create(kit.SPEC, EFFECT)
        return Acted()

    def release(
        unit: kit.Unit, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext
    ) -> Step:
        effects.owned(ports.ResourceOwned).stop(handle, kit.STOP_EFFECT)
        return Acted()

    return kit.Unit(decl, observe, advance or create, release)


@dataclass
class TreeRig:
    """`loopkit.Rig` for a tree: the run directory, the entry the loop walks, the admitted plan,
    the real child services (lane) under a manual clock."""

    rig: kit.Rig
    marker: PathMarker
    units: Mapping[str, object] = field(default_factory=dict)

    @property
    def run_dir(self) -> Path:
        return self.rig.run_dir

    @property
    def plan(self) -> compiler.AdmittedPlan:
        return self.rig.plan

    def run(self, host_scope: Any = None) -> None:
        walked = self.rig.loop()
        if host_scope is not None:
            walked.host_scope = host_scope  # the host sections' current readings (V-3.1)
        walked.run()

    def rows(self) -> list[dict[str, Any]]:
        lane = self.rig.lane()
        assert not lane.problems and not lane.torn, lane.problems
        return [row.entry for row in lane.rows]

    def ends(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in self.rows():
            if row["class"] == "end":
                assert row["path"] not in out, f"a second NodeEnd for {row['path']!r}"
                out[row["path"]] = row
        return out


def tree_rig(
    tmp_path: Path,
    root: AllDeclaration,
    units: Mapping[str, object],
    marker: PathMarker | None = None,
    *,
    deadline_s: float = TREE_DEADLINE_S,
    shown: Callable[[compiler.AdmittedPlan], compiler.AdmittedPlan] | None = None,
    port_impl: Mapping[type, object] | None = None,
    run_id: str = "r_tree_0001",
    request: Mapping[str, Any] | None = None,
) -> TreeRig:
    """Admit (compile, carve) `root` over `units` and build the loop's rig around it: the same
    plan is what admission would write and what the loop walks."""
    marker = marker if marker is not None else PathMarker()
    entry = WorkflowEntry(
        root=root.unit,
        units={root.unit: root, **units},
        deadline=timedelta(seconds=deadline_s),
    )
    admitted = admit_plan(entry, deadline_s, request or {})
    run_dir = tmp_path / run_id
    (run_dir / "work").mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
    clock = kit.ManualClock()
    cancel = kit.RigCancel(clock)
    sink = kit.Sink()
    deadline = kit.NOW + timedelta(seconds=deadline_s)
    inner = rs.build_run_services(
        rs.ServicesInput(
            run_dir=run_dir,
            plan=admitted,
            deadline=deadline,
            event=lambda kind, **fields: None,
            event_max=64 * 1024,
            now=lambda: clock.now,
            reserve_s=kit.RESERVE_S,
            margin_s=kit.MARGIN_S,
        )
    )
    services = kit.RigServices(
        inner,
        clock,
        cancel,
        sink,
        deadline=deadline,
        release_slice_s=admitted.release_slice,
        plan=shown(admitted) if shown is not None else None,
    )
    rig = kit.Rig(
        run_dir=run_dir,
        entry=entry,
        unit=next(iter(units.values())),
        plan=admitted,
        services=services,
        clock=clock,
        cancel=cancel,
        sink=sink,
        ports=dict(port_impl) if port_impl is not None else port_map(marker),
        intent=dict(request or {}),
    )
    return TreeRig(rig, marker, dict(units))


def admit_plan(
    entry: WorkflowEntry, deadline_s: float, request: Mapping[str, Any]
) -> compiler.AdmittedPlan:
    """The plan admission writes for `entry` and `request`: compiled, carved, digest attached
    (`loopkit.admit` with the request's arguments)."""
    _, tree = extract_root(entry)
    compiled = compiler.compile(tree, request)
    assert isinstance(compiled, compiler.AdmittedPlan), compiled
    release_slice = carving.release_slice_for(compiled, kit.RELEASE_SLICE_S)
    slices = carving.carve(compiled, deadline_s, kit.RESERVE_S, release_slice)
    assert isinstance(slices, dict), slices
    return carving.attach(compiled, slices, release_slice)


def wait_for(event: threading.Event) -> bool:
    return event.wait(tolerances.JOIN_WAIT_S)


def peak_running(rows: list[dict[str, Any]], paths: list[str]) -> int:
    """The most leaves that are running at once, from record order alone: a leaf runs from its
    first lane entry to its `NodeEnd`."""
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for n, row in enumerate(rows):
        path = row.get("path")
        if path in paths:
            first.setdefault(path, n)
            if row["class"] == "end":
                last[path] = n
    events = sorted([(first[p], 1) for p in first] + [(last[p], -1) for p in last if p in first])
    peak = running = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


def answer_of(tree: TreeRig) -> Any:
    """The host's projected answer (B4-C1) over the lane the loop wrote, the release pass having
    confirmed the run group gone."""
    from trestle.server import answer, fold, sweep
    from trestle.server.sweep import CleanupDisposition

    folded = fold.fold_lane(tree.run_dir, tree.plan)
    return answer.project(
        folded,
        CleanupDisposition(released=(sweep.GROUP_TARGET,)),
        type("Gone", (), {"confirmed_gone": True})(),
        tree.plan,
        False,
        None,
    )


def fixture_entry(name: str, edits: Mapping[str, str] | None = None) -> WorkflowEntry:
    """A structural fixture's `ENTRY`, its source first edited by replacing each key of `edits`
    (which must occur once) with its value."""
    source = fixture_source(name)
    for old, new in (edits or {}).items():
        assert source.count(old) == 1, f"{name}: {old!r} moved"
        source = source.replace(old, new)
    module = ModuleType(f"tree_fixture_{name}")
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)  # noqa: S102
    entry = module.ENTRY
    assert isinstance(entry, WorkflowEntry)
    return entry


def rig_of_entry(
    tmp_path: Path,
    entry: WorkflowEntry,
    marker: PathMarker | None = None,
    *,
    behaviour: Mapping[str, kit.Unit] | None = None,
    port_impl: Mapping[type, object] | None = None,
    request: Mapping[str, Any] | None = None,
    keep_units: bool = False,
) -> TreeRig:
    """`tree_rig` over a fixture's declared tree: each leaf unit becomes a scripted `leaf_unit`
    over the fixture's own declaration (`behaviour` overrides by name), each composite is kept."""
    root = entry.units[entry.root]
    assert isinstance(root, AllDeclaration)
    units: dict[str, object] = {}
    for name, unit in entry.units.items():
        if name == entry.root:
            continue
        if isinstance(unit, (AllDeclaration,)) or keep_units:
            units[name] = unit
        elif behaviour is not None and name in behaviour:
            units[name] = behaviour[name]
        else:
            declared = unit.declare()  # type: ignore[attr-defined]
            units[name] = leaf_unit(name, declaration=declared)
    return tree_rig(
        tmp_path,
        root,
        units,
        marker,
        deadline_s=entry.deadline.total_seconds(),
        port_impl=port_impl,
        request=request,
    )


class Recorded:
    """`ports.HasRecordedResult`."""

    def __init__(self, result: RecordedResult) -> None:
        self.recorded = result


class EventPort:
    """An event port (`kit.RunPort`) shared by every leaf of a tree: each `run` answers the next
    scripted `(status, recorded result, code)` (the last repeats) and is logged with the calling
    node's path, so two roots that share one environment share one port."""

    def __init__(
        self, *script: tuple[ConfirmationStatus, RecordedResult | None, str | None]
    ) -> None:
        self._script = list(script)
        self._lock = threading.Lock()
        self.calls: list[str] = []

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        return ports.InRunGroup()

    def run(self, name: str, ticket: Any) -> tuple[Confirmation, ports.HasRecordedResult | None]:
        with self._lock:
            n = len(self.calls)
            self.calls.append("/".join(ticket.lineage.path.segments))
        status, result, code = self._script[min(n, len(self._script) - 1)]
        return Confirmation(status, code, None), None if result is None else Recorded(result)


def recorded_unit(
    name: str,
    *,
    repeat: Repeat = Repeat.SAFE,
    retryable: frozenset[str] = frozenset(),
    max_attempts: int = 3,
) -> kit.Unit:
    """A RECORDED leaf: its completion is the recorded result of its one event effect (`run`)."""
    decl = replace(
        kit.declaration(
            completion=CompletionSource.RECORDED,
            repeat=repeat,
            effects=(kit.effect(kit.RUN_EFFECT, EffectFacetClass.EVENT, release_timeout_s=None),),
            retryable=retryable,
            max_attempts=max_attempts,
            budget_s=LEAF_BUDGET_S,
            max_wait_s=5.0,
        ),
        unit=name,
    )

    def observe(unit: kit.Unit, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        return kit.observation()

    def run(
        unit: kit.Unit, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext
    ) -> Step:
        effects.event(kit.RunPort).run("t", kit.RUN_EFFECT)
        return Acted()

    return kit.Unit(decl, observe, run)


def held_unit(
    name: str,
    gate: threading.Event,
    *,
    on_hold: Callable[[str], None] = lambda path: None,
    budget_s: float = LEAF_BUDGET_S,
) -> kit.Unit:
    """A leaf that creates its marker and then holds: once the marker is present its observation
    calls `on_hold(name)` (the first time) and waits at `gate`, and it never turns ready. A test
    raises a flag, or moves the clock, while every such leaf is inside its wait."""
    base = leaf_unit(name, budget_s=budget_s)
    arrived: list[str] = []

    def observe(unit: kit.Unit, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ports.ResourceReads)
        seen = resource.observe(kit.SPEC, ctx.lineage, EFFECT)
        if seen.selector_present:
            if not arrived:
                arrived.append(name)
                on_hold(name)
            assert gate.wait(tolerances.JOIN_WAIT_S)
        return kit.observation(selector_present=seen.selector_present, ready=False)

    def advance(
        unit: kit.Unit, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext
    ) -> Step:
        return base.advance(params, state, effects, ctx)

    def release(
        unit: kit.Unit, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext
    ) -> Step:
        return base.release(params, handle, effects, ctx)

    return kit.Unit(base.decl, observe, advance, release)
