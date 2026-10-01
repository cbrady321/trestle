"""L.TR-L.10: WR-UNIT-7's roll-up through one host call (J-TRL; MC-12, MC-17, MC-B3-01, MC-B3-02).

Every case is one MCP `run` call on a `trestle serve` subprocess (`hostpath.mcp_run_tree`, never
MC-26): the published tree is admitted by the host, driven by the tree walk in the plugin's own
process and answered by the host. The in-library twins are `tests/tree/test_tr4_rollup.py` and
`test_tr4_depth.py`; nothing here imports `tests/proof/harness.py`.

A structural fixture is declaration only (MC-B3-01), so a host run of it needs behaviour. `behaving`
rewrites the published source of a fixture or of a `GENERATED` tree (never the declaration's shape)
so that every leaf is a marker-creating unit, one leaf of which ends as the case says (`BEHAVIOUR`,
one source line); the entry function makes the loop's one call. The unit adds the marker effects to
whatever the leaf declares, so a permuted or wrapped tree keeps its own child order, needs and
budgets, and a fixture's own remedy effects. The behaviour fixtures (`depth2_conditions`,
`failure_dependents`) are published as they are, with one source line or a few constants changed.

The stop cases (`test_root_stops_depth2_active`, `test_root_stop_after_ordinary_failure`) need a
node that is busy when the stop arrives: it creates its marker and waits on its cancellation, so it
is still active at a root cancel, at the deadline's release point (a leaf cannot outlive its carved
slice by design, so the tree's own node or its subtree may have ended `carve_exceeded` first: the
answer's `stopped`/`unended`/`not_started` listing covers each, V-8 L-8) and at a server restart.
The deadline cases run with the operator's own time knobs shortened (`short_host`), so a deadline is
seconds; the cancel and restart cases keep the published budgets, so a slow machine cannot turn a
cancel into a deadline.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.trees import generators
from tests.proof import mcp_host, records, tolerances
from tests.tree import d7_answers as d7a
from tests.tree import hostpath
from tests.tree.test_tr4_depth import EXPECTED
from trestle.common import codes
from trestle.common.plan import vocabulary as vocab
from trestle.server import answer as answer_mod
from trestle.server import fold

REPO = Path(__file__).resolve().parents[3]
TREES = REPO / "tests" / "fixtures" / "trees"
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6
ENV = "dev"
SHORT_RELEASE_SLICE_S = 3
SHORT_RESERVE_S = 1
SHORT_MARGIN_S = 40
SHORT_BUDGET_DIVISOR = 10
SHORT_DEADLINE_S = 16  # root budget 12 + release slice 3 + 1
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")

proves_clause = pytest.mark.proves("A1.5", "A1.5", "A", "tree", "MCP", "CI")
proves_permutation = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:permutation-invariant", "A", "tree", "LOGIC+MCP", "CI"
)
proves_wrapper = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:depth-invariant-wrapper-any-name", "A", "tree", "LOGIC+MCP", "CI"
)
proves_completion = pytest.mark.proves(
    "WR-TERM-4", "WR-TERM-4:permuted-completion", "A", "tree", "LOGIC", "CI"
)
proves_terminates = pytest.mark.proves(
    "WR-TERM-3", "WR-TERM-3:tree", "A", "tree", "MCP+PROC+STUB", "CI"
)
proves_depth2 = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:node-local-conditions-depth2", "A", "tree", "LOGIC+MCP", "CI"
)
proves_stops = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:root-stops-depth2", "A", "tree", "LOGIC+MCP", "CI"
)
proves_reached = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:root-stop-lists-reached-failure", "A", "tree", "LOGIC+MCP", "CI"
)
proves_child_view = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:child-view-equals-root", "A", "tree", "LOGIC+MCP", "CI"
)
proves_listed = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:race-conditions-listed", "A", "tree", "LOGIC+MCP", "CI"
)

# ---- the behaviour source ------------------------------------------------------------------------

_UNIT = '''class Unit:
    """A generated or structural leaf made to act (L.TR-L.10): it creates its own marker and is
    satisfied once it is there, unless `BEHAVIOUR` says how this unit ends instead."""

    def __init__(self, declaration: LeafDeclaration) -> None:
        self._declaration = declaration
        self._unit = declaration.unit
        self._paused = False
        self._spec = ResourceSpec(
            self._unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "rollup-" + self._unit, None
        )

    def _how(self) -> tuple[str, float]:
        return BEHAVIOUR.get(self._unit, ("pass", 0.0))

    def declare(self) -> LeafDeclaration:
        base = self._declaration
        have = {e.effect for e in base.effects}
        extra = tuple(e for e in EFFECTS if e.effect not in have)
        return replace(base, effects=base.effects + extra)

    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:
        how, pause = self._how()
        if not self._paused:
            self._paused = True
            if pause:
                ctx.cancellation.wait(timedelta(seconds=pause))
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)
        checked = resource.check("ready", seen.selector_ref) if seen.selector_ref else None
        ready = how != "hold" and checked is not None and checked.satisfied
        if how == "hold" and seen.selector_present and TMP is not None:
            # created its marker (an entry is on the lane), now busy until it is stopped
            (TMP / ("active-" + self._unit)).write_text("1", encoding="utf-8")
            ctx.cancellation.wait(timedelta(seconds=HOLD_WAIT_S))
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(ready, None, ""),
            preconditions=tuple(True for _ in self._declaration.preconditions),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        how = self._how()[0]
        if how == "failed":
            return Failed("fixture.broke", "the case's trigger")
        if how == "blocked":
            return Blocked(
                "fixture.blocked", "Fix the fixture, then re-send.", Resend.WILL_NOT_SUCCEED
            )
        if how == "raise":
            raise RuntimeError("fixture: the unit raised")
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()
'''

_IMPORTS = """from dataclasses import replace

from trestle_packs.fakes import FakeMarker

from trestle.workflow import (
    EffectDeclaration,
    EffectFacetClass,
    Lifetime,
    RealizationKind,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
from trestle.workflow.units import Acted, Blocked, Failed
from trestle.workflow.values import CheckResult, FoundRef, Observation, Resend
"""

_CONSTANTS = """CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
HOLD_WAIT_S = __HOLD__
TMP: Any = None
BEHAVIOUR: dict[str, tuple[str, float]] = __BEHAVIOUR__
EFFECTS = (
    EffectDeclaration(
        CREATE_EFFECT, EffectFacetClass.CREATE, "", Lifetime.RUN, frozenset(), timedelta(seconds=1)
    ),
    EffectDeclaration(
        STOP_EFFECT,
        EffectFacetClass.OWNED,
        "",
        Lifetime.RUN,
        frozenset(),
        timedelta(seconds=1),
        is_release=True,
    ),
)
"""

_UNIT_BLOCK = re.compile(r"^class Unit:\n(?:(?:[ \t]+.*)?\n)*", re.MULTILINE)
_ENTRY_RETURN = re.compile(r"^    return \{.*\}\n\Z", re.MULTILINE)
_BUDGET = re.compile(r"budget=timedelta\(seconds=(\d+)\)")
_DECORATOR = re.compile(r"@trestle\((deadline=\d+)\)")
_SIGNATURE = re.compile(r"\(ctx: Context\)")
_ENV_ROOT = """ENTRY = WorkflowEntry(
    root=ENTRY.root,
    units={**ENTRY.units, ENTRY.root: replace(ENTRY.units[ENTRY.root], env_key_field="env")},
    deadline=ENTRY.deadline,
)"""
_ANCHOR = "from trestle.plugin import Context, trestle\n"


def rename(source: str, old: str, new: str) -> str:
    """`source` published under the plugin name `new` (the entry function's name)."""
    body, count = re.subn(rf"^def {re.escape(old)}\(", f"def {new}(", source, flags=re.MULTILINE)
    assert count == 1, old
    return body


def behaving(
    source: str,
    behaviour: dict[str, tuple[str, float]],
    *,
    hold_wait_s: int = 100,
    deadline_s: int | None = None,
    budget_divisor: int = 1,
) -> str:
    """`source` (a structural fixture or a `GENERATED` tree) with its `Unit` made to act as
    `behaviour` says (unit name -> `(how, pause_s)`; `how` is `pass`, `failed`, `blocked`, `raise`
    or `hold`, a hold never being satisfied within `hold_wait_s`) and its entry function making
    the loop's one call."""
    assert _UNIT_BLOCK.search(source), "no structural Unit class to replace"
    body = _UNIT_BLOCK.sub(lambda _m: _UNIT + "\n\n", source, count=1)
    constants = _CONSTANTS.replace("__HOLD__", str(hold_wait_s)).replace(
        "__BEHAVIOUR__", repr(behaviour)
    )
    assert _ANCHOR in body
    body = body.replace(_ANCHOR, _IMPORTS + "\n" + _ANCHOR, 1)
    # the constants come after every import, ahead of the first class
    assert "\n\nclass Unit:" in body
    body = body.replace("\n\nclass Unit:", "\n\n" + constants + "\n\nclass Unit:", 1)
    if budget_divisor > 1:
        body = _BUDGET.sub(
            lambda m: f"budget=timedelta(seconds={max(3, int(m.group(1)) // budget_divisor)})", body
        )
    if deadline_s is not None:
        body, moved = re.subn(
            r"deadline=timedelta\(seconds=\d+\)", f"deadline=timedelta(seconds={deadline_s})", body
        )
        assert moved == 1
        body, moved = re.subn(r"@trestle\(deadline=\d+\)", f"@trestle(deadline={deadline_s})", body)
        assert moved == 1
    entry, count = _ENTRY_RETURN.subn(
        lambda m: (
            "    global TMP\n"
            "    TMP = ctx.tmp\n"
            "    marker = FakeMarker(ctx.tmp / 'markers', 'run')\n"
            "    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}\n"
            "    run_tree(ctx, ENTRY, {'env': env}, ports=ports)\n" + m.group(0)
        ),
        body,
    )
    assert count == 1, "the entry function is not the expected one-line return"
    # the tree's ports make it environment-bound (WR-OWN-8): the root names the `env` argument
    entry, decorated = _DECORATOR.subn(r'@trestle(\1, env_arg="env")', entry, count=1)
    assert decorated == 1
    entry, signed = _SIGNATURE.subn(r'(ctx: Context, env: str = "dev")', entry, count=1)
    assert signed == 1
    return entry.replace("\n\n@trestle(", "\n\n" + _ENV_ROOT + "\n\n@trestle(", 1)


def fixture_source(name: str) -> str:
    return (TREES / f"{name}.py").read_text(encoding="utf-8")


def tree_source(name: str) -> str:
    """The published source of a base fixture or a generated tree by name."""
    return d7a.tree_named(name).source


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[mcp_host.McpHost]:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as server:
        yield server


@pytest.fixture
def short_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[mcp_host.McpHost]:
    """A host whose root deadline can be short: the release slice, the finalization reserve and
    the margin are the operator's own environment knobs (`trestle.common.clock`), read by the
    server and by every child it starts, so a run is admitted with a deadline of seconds."""
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("TRESTLE_RELEASE_SLICE_S", str(SHORT_RELEASE_SLICE_S))
    monkeypatch.setenv("TRESTLE_FINALIZATION_RESERVE_S", str(SHORT_RESERVE_S))
    monkeypatch.setenv("TRESTLE_FINALIZATION_MARGIN_S", str(SHORT_MARGIN_S))
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as server:
        yield server


def plant(server: mcp_host.McpHost, name: str, source: str) -> None:
    (server.home / "plugins" / f"{name}.py").write_text(source, encoding="utf-8")


def run_together(server: mcp_host.McpHost, sources: dict[str, str]) -> dict[str, dict[str, Any]]:
    """One MCP `run` call per plugin, all in flight at once (each on its own environment, so no
    two share a lease); the wire run views by plugin name."""
    held: dict[str, int] = {}
    for index, (name, source) in enumerate(sources.items()):
        plant(server, name, source)
        held[name] = server.hold(
            "run",
            {
                "plugin": name,
                "args": {"env": f"{ENV}{index}"},
                "wait_ms": hostpath.WAIT_MS,
                "completion": "terminal",
            },
        )
    views: dict[str, dict[str, Any]] = {}
    for name, req in held.items():
        wired = server.join(req)
        assert isinstance(wired, dict), wired
        assert "code" not in wired and str(wired["run_id"]).startswith("r_"), (name, wired)
        assert wired["state"] in TERMINAL_STATES, (name, wired)
        views[name] = wired
    return views


def wrapper_of(name: str) -> str | None:
    """The wrapper segment a generated wrapping adds to the tree's paths, else None."""
    return name.partition("__")[2] or None


def triggered_source(name: str) -> str:
    """Tree `name` (a base fixture or a generated variant) made to act, its d7 trigger failing."""
    entry = d7a.tree_named(name).entry
    trigger = d7a.trigger_of(name, entry)
    return behaving(tree_source(name), {trigger: ("failed", 0.0)})


def logical(name: str, primary: dict[str, Any]) -> list[str]:
    """The answer's primary path with the generated wrapper's own segment elided (B4-C4)."""
    wrapper = wrapper_of(name)
    return [seg for seg in primary["path"] if seg != wrapper]


@proves_clause
@proves_permutation
@proves_wrapper
@proves_completion
@proves_terminates
def test_permutation_and_depth_same_class_primary(host: mcp_host.McpHost) -> None:
    """One MCP call per tree over `GENERATED`'s permutations and wrappings (and their bases): the
    root class and the logical primary path are those of the base fixture whatever the child order
    or the wrapper's name, the wrapped node's own path gains exactly the wrapper's segment, and
    every run reaches a terminal state (B4-C4's ordinal property, WR-TERM-4)."""
    names = [t.name for t in generators.GENERATED if not t.name.startswith("hundred_node")]
    bases = sorted({d7a.base_of(name) for name in names})
    views = run_together(host, {name: triggered_source(name) for name in [*bases, *names]})
    want: dict[str, tuple[str, list[str]]] = {}
    for base in bases:
        answer = views[base]["answer"]
        assert answer["outcome"] == "failed", answer
        want[base] = (answer["outcome"], logical(base, answer["primary"]))
    for name in names:
        base = d7a.base_of(name)
        answer = views[name]["answer"]
        assert (answer["outcome"], logical(name, answer["primary"])) == want[base], name
        assert answer["primary"]["listing"] == "candidate"
        if wrapper_of(name):
            # only the wrapped node's path gains the wrapper's segment: it is the trigger's
            path = answer["primary"]["path"]
            assert path.count(wrapper_of(name)) == 1 and path[-1] == want[base][1][-1], path


PAUSE_S = tolerances.SETTLE_LONG_S * 1.5  # the later trigger waits (cancellably): a fixed order


def reached(answer: dict[str, Any]) -> list[tuple[tuple[str, ...], str | None]]:
    """Every node that reached a condition, primary first: (path, node_class)."""
    nodes = [answer["primary"], *answer["listed"]]
    return [(tuple(n["path"]), n["node_class"]) for n in nodes if n["listing"] == "candidate"]


def race_source(name: str, left: tuple[str, float], right: tuple[str, float]) -> str:
    return rename(
        behaving(tree_source("race_two_trigger"), {"left": left, "right": right}),
        "race_two_trigger",
        name,
    )


@proves_clause
@proves_listed
def test_two_trigger_race_lists_reached_conditions(host: mcp_host.McpHost) -> None:
    """`race_two_trigger` through one MCP call per case: both triggers are reached before the run
    ends, so the root answer lists every condition reached, the primary first and the other beside
    it with its own class (a failed branch does not stop the root, OQ-33; an exception, which does,
    still lists the failed branch that ended before it). Run in both arrival orders (the later
    trigger waits), the same conditions give the same class, primary and listing."""
    cases = {
        "failed_blocked": ("failed", "blocked"),
        "failed_failed": ("failed", "failed"),
    }
    sources: dict[str, str] = {}
    for label, (first, second) in cases.items():
        sources[f"race_{label}_lr"] = race_source(
            f"race_{label}_lr", (first, 0.0), (second, PAUSE_S)
        )
        sources[f"race_{label}_rl"] = race_source(
            f"race_{label}_rl", (first, PAUSE_S), (second, 0.0)
        )
    sources["race_exception"] = race_source("race_exception", ("failed", 0.0), ("raise", PAUSE_S))
    views = run_together(host, sources)

    for label, (first, second) in cases.items():
        one = views[f"race_{label}_lr"]["answer"]
        two = views[f"race_{label}_rl"]["answer"]
        # both conditions are in the answer, whichever arrived first
        for answer in (one, two):
            classes = dict(reached(answer))
            assert sorted(path for path, _ in classes.items()) == [("left",), ("right",)], answer
            assert classes == {("left",): first, ("right",): second}, (label, classes)
            assert answer["primary"]["listing"] == "candidate"
            assert answer["primary"]["path"] == ["left"], answer["primary"]  # key-min, then ordinal
            assert answer["outcome"] == "failed"
        # ... and the schedule's order changes nothing in the root answer
        for field in ("outcome", "primary", "listed", "incomplete", "error"):
            assert one[field] == two[field], (label, field)

    raised = views["race_exception"]["answer"]
    assert raised["outcome"] == "execution_error", raised
    assert raised["primary"]["path"] == ["right"]
    assert raised["primary"]["code"] == codes.UNIT_RAISED
    # the trigger that ended before the stop is still there, listed with its own class
    assert dict(reached(raised)) == {("right",): "execution_error", ("left",): "failed"}, raised


# ---- depth-two conditions --------------------------------------------------------------------

DEPTH2 = TREES / "depth2_conditions.py"
_CONDITION = re.compile(r'^CONDITION = "[a-z_]+"$', re.MULTILINE)


def depth2_source(condition: str, name: str, *, depth: int) -> str:
    """The `depth2_conditions` fixture with its one `CONDITION` line set, published as `name`:
    `depth` 2 is the fixture as authored (`probe` at `stage/probe`), `depth` 0 the same leaf alone
    as a one-vertex root (its declaration is the fixture's own)."""
    source, count = _CONDITION.subn(f'CONDITION = "{condition}"', DEPTH2.read_text("utf-8"))
    assert count == 1
    if depth == 0:
        alone = (
            "from dataclasses import replace as _replace\n"
            "\n\n"
            "class RootProbe(Probe):\n"
            "    def declare(self) -> LeafDeclaration:\n"
            '        return _replace(super().declare(), env_key_field="env")\n'
            "\n\n"
            "ENTRY = WorkflowEntry(\n"
            '    root="probe",\n'
            '    units={"probe": RootProbe(CONDITION)},\n'
            "    deadline=timedelta(seconds=DEADLINE_S),\n"
            ")\n"
            "\n\n"
        )
        source = source.replace("\n\n@trestle(", "\n\n" + alone + "@trestle(", 1)
    return rename(source, "depth2_conditions", name)


@proves_clause
@proves_depth2
def test_depth2_conditions_one_call(host: mcp_host.McpHost) -> None:
    """Each of the six inducers of `depth2_conditions` through one MCP call, at `stage/probe` and
    (the twin) alone as a root: the answer's class and the probe's own `node_class`, code and
    condition are the same at depth two as at depth zero, and are B4-T2/T3's transcribed values
    (`never_ready` is `exhausted`, answered `blocked`)."""
    sources: dict[str, str] = {}
    for condition in EXPECTED:
        sources[f"deep_{condition}"] = depth2_source(condition, f"deep_{condition}", depth=2)
        sources[f"flat_{condition}"] = depth2_source(condition, f"flat_{condition}", depth=0)
    views = run_together(host, sources)
    for condition, (outcome, klass, code) in EXPECTED.items():
        deep = views[f"deep_{condition}"]["answer"]
        flat = views[f"flat_{condition}"]["answer"]
        assert deep["outcome"] == flat["outcome"] == outcome, (condition, deep, flat)
        if condition == "pass":  # a success is answered by the rolled-up root
            assert deep["primary"]["path"] == [] and deep["primary"]["listing"] == "rolled_up"
            continue
        here, alone = deep["primary"], flat["primary"]
        assert here["path"] == ["stage", "probe"] and alone["path"] == [], (condition, here, alone)
        assert here["listing"] == alone["listing"] == "candidate"
        assert here["node_class"] == alone["node_class"] == klass, condition
        assert (here["condition"], here["code"]) == (alone["condition"], alone["code"])
        assert here["code"] == code, (condition, here)
        if condition == "never_ready":
            assert deep["outcome"] == "blocked" and here["node_class"] == "exhausted"


# ---- root-addressed stops with a depth-two node active ---------------------------------------

STOP_WAIT_MS = int(tolerances.JOIN_WAIT_S * 4 * 1000)


def start(server: mcp_host.McpHost, name: str, source: str, env: str) -> int:
    """`run` in flight (terminal completion), its request id."""
    plant(server, name, source)
    return server.hold(
        "run",
        {
            "plugin": name,
            "args": {"env": env},
            "wait_ms": STOP_WAIT_MS,
            "completion": "terminal",
        },
    )


def run_dir_of_active(server: mcp_host.McpHost, unit: str, seen: set[str]) -> Path:
    """The run directory (not one of `seen`, the runs already dealt with) whose `unit` has created
    its marker and is busy (it says so in the run's tmp); `seen` gains it."""
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < deadline:
        found = [
            p.parents[2]
            for p in sorted(server.home.glob(f"runs/**/tmp/active-{unit}"))
            if p.parents[2].name not in seen
        ]
        if found:
            seen.add(found[0].name)
            return found[0]
        time.sleep(tolerances.POLL_S)
    raise AssertionError(f"{unit} never became active")


def wait_end(run_dir: Path, path: str) -> bool:
    """Whether the run's lane records `path`'s `NodeEnd` within the join wait."""
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < deadline:
        if any(r.cls == "end" and r.path == path for r in records.lane_rows(run_dir).rows):
            return True
        time.sleep(tolerances.POLL_S)
    return False


def await_terminal(server: mcp_host.McpHost, run_id: str) -> dict[str, Any]:
    """The run view once the run is terminal (`await_runs`)."""
    wired = server.call("await_runs", {"run_ids": [run_id], "timeout_ms": STOP_WAIT_MS})
    (view,) = wired["result"]
    assert isinstance(view, dict), wired
    return view


def listing_of(answer: dict[str, Any]) -> dict[tuple[str, ...], str]:
    return {tuple(n["path"]): n["listing"] for n in [answer["primary"], *answer["listed"]]}


def plan_of(run_dir: Path) -> Any:
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    plan = fold.plan_of_spec(spec)
    assert plan is not None
    return plan


def lowest_ordinal_stopped(run_dir: Path, answer: dict[str, Any]) -> list[str]:
    """B4-C2's `incomplete`, from the admitted plan's ordinals and the answer's own listing."""
    ordinal: dict[tuple[str, ...], int] = {
        tuple(p.split("/")) if p else (): n for p, n in plan_of(run_dir).precedence_ordinal.items()
    }
    stopped = [p for p, listing in listing_of(answer).items() if listing in ("stopped", "unended")]
    first: tuple[str, ...] = min(stopped, key=lambda p: (ordinal[p], p))
    return list(first)


def held_stop_source(name: str, *, short: bool = False) -> str:
    """`aaa_wrap/left` (two levels below the root) creates its marker and stays busy until it is
    stopped; `right` passes and `join`, which needs both, waits behind `left`. `short` scales the
    budgets and the deadline to `SHORT_DEADLINE_S` (the deadline case)."""
    return rename(
        behaving(
            tree_source("two_branch_barrier__aaa_wrap"),
            {"left": ("hold", 0.0)},
            deadline_s=SHORT_DEADLINE_S if short else None,
            budget_divisor=SHORT_BUDGET_DIVISOR if short else 1,
        ),
        "two_branch_barrier__aaa_wrap",
        name,
    )


@proves_clause
@proves_stops
def test_root_stops_depth2_active(short_host: mcp_host.McpHost) -> None:
    """A root cancel, the deadline's release point, and a server restart, each with a node two
    levels below the root active (B4-C2 rules (1)-(2)): the class comes from the stop, never from a
    node's class; the primary is the root's own account; the active node is listed `stopped`,
    `unended` or `not_started`; the waiting `join` is `not_started`; and a release point says where
    the run was cut (`incomplete`)."""
    deep = ("aaa_wrap", "left")

    # cancel: a second call while the first is in flight, once the node is busy
    seen: set[str] = set()
    req = start(short_host, "stop_cancel", held_stop_source("stop_cancel"), "cancelenv")
    run_dir = run_dir_of_active(short_host, "left", seen)
    cancelled = short_host.call("cancel", {"run_id": run_dir.name})
    assert cancelled["code"] == codes.CANCEL_ACCEPTED, cancelled
    view = short_host.join(req, timeout=STOP_WAIT_MS / 1000)
    answer = view["answer"]
    assert answer["outcome"] == "cancelled" and answer["root_stop"] == "cancel", answer
    assert answer["primary"]["path"] == [] and answer["primary"]["node_class"] is None
    listing = listing_of(answer)
    assert listing[deep] in ("stopped", "unended", "not_started"), listing
    assert listing[("join",)] == "not_started"
    assert answer["incomplete"] is None

    # the deadline's release point: the same tree, nobody cancels
    started = time.monotonic()
    view = short_host.join(
        start(
            short_host,
            "stop_deadline",
            held_stop_source("stop_deadline", short=True),
            "deadlineenv",
        ),
        timeout=STOP_WAIT_MS / 1000,
    )
    assert time.monotonic() - started < SHORT_DEADLINE_S + tolerances.JOIN_WAIT_S
    seen.add(view["run_id"])
    answer = view["answer"]
    assert view["state"] == "timed_out", view
    assert answer["outcome"] == "timed_out" and answer["root_stop"] == "release_point", answer
    assert answer["primary"]["path"] == [] and answer["primary"]["node_class"] is None
    listing = listing_of(answer)
    assert listing[deep] in ("stopped", "unended", "not_started"), listing
    assert listing[("join",)] == "not_started"
    run_dir = short_host.home.glob(f"runs/*/{view['run_id']}").__next__()
    assert answer["incomplete"] == lowest_ordinal_stopped(run_dir, answer)

    # a restart: the server is killed with the node busy; the next boot's recovery answers the run
    start(short_host, "stop_restart", held_stop_source("stop_restart"), "restartenv")
    run_dir = run_dir_of_active(short_host, "left", seen)
    short_host.kill_server()
    short_host.restart()
    recovered = await_terminal(short_host, run_dir.name)
    assert recovered["state"] == "interrupted", recovered
    answer = recovered["answer"]
    assert answer["outcome"] == "execution_error" and answer["root_stop"] == "restart", answer
    assert answer["recovered"] is True
    assert answer["error"]["code"] == vocab.EXECUTION_RESTART, answer["error"]
    assert answer["primary"]["path"] == [] and answer["primary"]["node_class"] is None
    listing = listing_of(answer)
    assert listing[deep] in ("stopped", "unended", "not_started"), listing
    assert listing[("join",)] == "not_started"
    assert answer["cleanup"]["group_confirmed_gone"] is True  # the orphaned tree was stopped


def failure_source(name: str, *, holds: bool, short: bool = False) -> str:
    """`failure_dependents` (L.TR-3.5): `broken` fails at once, `left` and `right` (which need it)
    are cut, and `independent` either finishes (`holds` False) or creates its marker and stays busy
    until it is stopped (`holds` True; `short` scales its budgets and deadline to
    `SHORT_DEADLINE_S`)."""
    source = fixture_source("failure_dependents")
    if not holds:
        return rename(source, "failure_dependents", name)
    edits = [
        (
            '"independent": Node("independent", "works")',
            '"independent": Node("independent", "holds")',
        ),
        (
            "        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)\n",
            "        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)\n"
            '        if self._role == "holds" and seen.selector_present and TMP is not None:\n'
            '            (TMP / "active-independent").write_text("1", encoding="utf-8")\n'
            "            ctx.cancellation.wait(timedelta(seconds=HOLD_WAIT_S))\n",
        ),
        ("\nLEAF_BUDGET_S", "\nTMP: Any = None\nHOLD_WAIT_S = 100\nLEAF_BUDGET_S"),
        (
            '    marker = FakeMarker(ctx.tmp / "markers", "run")\n',
            "    global TMP\n    TMP = ctx.tmp\n"
            '    marker = FakeMarker(ctx.tmp / "markers", "run")\n',
        ),
    ]
    if short:
        edits += [
            ("LEAF_BUDGET_S = 10", "LEAF_BUDGET_S = 4"),
            ("ROOT_BUDGET_S = 40", "ROOT_BUDGET_S = 12"),
            ("DEADLINE_S = 60", f"DEADLINE_S = {SHORT_DEADLINE_S}"),
            ("@trestle(deadline=60,", f"@trestle(deadline={SHORT_DEADLINE_S},"),
        ]
    for old, new in edits:
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    return rename(source, "failure_dependents", name)


@proves_clause
@proves_reached
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_root_stop_after_ordinary_failure(short_host: mcp_host.McpHost, stop: str) -> None:
    """`failure_dependents` through one MCP call with `independent` still running: `broken` fails
    (only its dependents are cut, the root goes on, OQ-33), then the root is cancelled (or reaches
    its deadline). The class is the stop's (`cancelled`, resp. `timed_out`), the primary is the
    root's own account, and `broken`'s condition is listed beside it as `failed`, never as
    primary; its dependents are `not_started`, the running sibling `stopped` or `unended`."""
    name = f"failure_{stop}"
    source = failure_source(name, holds=True, short=stop == "deadline")
    req = start(short_host, name, source, f"{stop}env")
    if stop == "cancel":
        run_dir = run_dir_of_active(short_host, "independent", set())
        # `independent` holding says nothing of its sibling: on a loaded host `broken` may not
        # have started yet, and a cancel then lists it `not_started` (CK-8). Cancel once it ended.
        assert wait_end(run_dir, "broken"), "broken never ended"
        cancelled = short_host.call("cancel", {"run_id": run_dir.name})
        assert cancelled["code"] == codes.CANCEL_ACCEPTED, cancelled
    view = short_host.join(req, timeout=STOP_WAIT_MS / 1000)
    answer = view["answer"]
    want = "cancelled" if stop == "cancel" else "timed_out"
    assert answer["outcome"] == want, answer
    assert answer["root_stop"] == ("cancel" if stop == "cancel" else "release_point")
    assert answer["primary"]["path"] == [] and answer["primary"]["node_class"] is None
    (broken,) = [n for n in answer["listed"] if n["path"] == ["broken"]]
    assert broken["listing"] == "candidate" and broken["node_class"] == "failed", broken
    assert (broken["condition"], broken["code"]) == ("failed", "fixture.broke"), broken
    listing = listing_of(answer)
    assert listing[("left",)] == listing[("right",)] == "not_started", listing
    assert listing[("independent",)] in ("stopped", "unended"), listing
    if stop == "deadline":
        run_dir = next(short_host.home.glob(f"runs/*/{view['run_id']}"))
        assert answer["incomplete"] == lowest_ordinal_stopped(run_dir, answer)


# ---- child views -----------------------------------------------------------------------------


def child_views(server: mcp_host.McpHost, run_dir: Path) -> dict[tuple[str, ...], dict[str, Any]]:
    """Every non-root vertex's child view (V-1.3), read by its derived handle over one MCP
    `await_runs` call each."""
    views: dict[tuple[str, ...], dict[str, Any]] = {}
    for text in plan_of(run_dir).precedence_ordinal:
        if not text:
            continue
        path = tuple(text.split("/"))
        view = await_terminal(server, answer_mod.child_handle(run_dir.name, path))
        assert view["root_run_id"] == run_dir.name and view["path"] == text, view
        views[path] = view
    return views


@proves_clause
@proves_child_view
def test_child_view_class_equals_root_account(host: mcp_host.McpHost) -> None:
    """Whatever ended a tree (an ordinary failure with dependents cut, a raising node beside a
    failed one, a wrapped tree's trigger), each child view's answer is the root answer's own account
    of that node, byte for byte: one roll-up rule (B4-C8), the same `node_class`, condition, code
    and listing, and the view is terminal like its root."""
    sources = {
        "child_dependents": failure_source("child_dependents", holds=False),
        "child_exception": race_source("child_exception", ("failed", 0.0), ("raise", PAUSE_S)),
        "child_wrapped": rename(
            triggered_source("two_branch_barrier__aaa_wrap"),
            "two_branch_barrier__aaa_wrap",
            "child_wrapped",
        ),
    }
    views = run_together(host, sources)
    for name, root in views.items():
        answer = root["answer"]
        account = {tuple(n["path"]): n for n in [answer["primary"], *answer["listed"]]}
        assert answer["listed_count"] == len(answer["listed"]), "the answer is truncated"
        run_dir = next(host.home.glob(f"runs/*/{root['run_id']}"))
        children = child_views(host, run_dir)
        assert children, name
        for path, view in children.items():
            assert view["state"] == root["state"], (name, path, view)
            assert path in account, (name, path, sorted(account))
            assert view["answer"] == account[path], (name, path, view["answer"], account[path])
