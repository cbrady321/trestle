"""L.TR-4.6: node-local conditions two levels deep (B4-T2) and root-addressed stops with a depth-two
node active (B4-C2 rules (1)-(2)).

The host answer's classification does not look at depth: a node at `stage/probe` gets the same
`node_class` (B4-T2) as the same node alone as a root. One variant only: OQ-32 is answered, so a
node that exhausts (row 8, J-20 "service never ready") is `EXHAUSTED` at rank 4 and its outcome is
`BLOCKED` (B4-T1, B4-T3, B4-I2). A cancel, a release-point deadline or a restart with a depth-two
node active is decided outside the key by rules (1)-(2): the answer's class comes from the stop
(`outcome`), never from `primary.node_class`, the primary is the root's own account, and the active
node is listed `STOPPED`, `UNENDED` or `NOT_STARTED` (V-8 L-8). A root stop after an ordinary
failure (which does not stop the root, OQ-33) keeps the stop's class and lists the failed node's
condition beside it, never as primary.

In-library over the real loop (MC-26's rig: real lane and services, a manual clock) with the host's
stop row written into the folded record as the conductor does (B2-C15); a restart is a planted
in-flight run finalized by `recover_run_dir`. The host cancel form is `test_tr3_failfast`'s."""

from __future__ import annotations

import importlib.util
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.fixtures.trees import generators
from tests.proof import tolerances
from tests.tree import runs
from tests.tree import treekit as tk
from trestle.common import lane_format as lf
from trestle.common.outcome import OutcomeClass
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.vocabulary import Listing, NodeClass, RootStop
from trestle.server import answer, fold, sweep
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.main import Kernel
from trestle.server.recovery import recover_run_dir
from trestle.server.sweep import CleanupDisposition
from trestle.workflow.units import Failed
from trestle.workflow.values import StopCause

proves_conditions = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:node-local-conditions-depth2", "A", "tree", "LOGIC+MCP", "CI"
)
proves_stops = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:root-stops-depth2", "A", "tree", "LOGIC+MCP", "CI"
)
proves_reached = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:root-stop-lists-reached-failure", "A", "tree", "LOGIC+MCP", "CI"
)
proves_not_started = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:not-started-reported", "A", "tree", "LOGIC+MCP", "CI"
)

FIXTURE = tk.TREES / "depth2_conditions.py"
DEEP = ("stage", "probe")

# B4-T2 / B4-T3 transcribed (not read back from the product's tables): condition -> (outcome, node
# class, code). The class is the row that matches; the outcome is B4-T3 of that class, `EXHAUSTED`
# answering `blocked` (OQ-32, one variant).
EXPECTED: dict[str, tuple[str, str, str | None]] = {
    "pass": ("passed", "passed", None),
    "assertion": ("failed", "failed", "test.assertion_failed"),
    "mfa": ("blocked", "blocked", "execution.credential_interactive"),
    "never_ready": ("blocked", "exhausted", "execution.postcondition_timeout"),
    "no_progress": ("blocked", "blocked", "execution.remedy_no_progress"),
    "exception": ("execution_error", "execution_error", "execution.unit_raised"),
}


def load_fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tree_fixture_depth2_conditions", FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rig_at_depth(depth: int, condition: str, directory: Path) -> tk.TreeRig:
    """The fixture's `probe` (`condition`) two levels down (`depth` 2: `app/stage/probe`) or alone
    as a root (`depth` 0), on the fake ports the fixture names."""
    module = load_fixture()
    ports = module.make_ports(condition, directory)
    request = {"env": "dev"}
    if depth == 0:
        return tk.tree_rig(
            directory / "run",
            module.Probe(condition),
            {},
            port_impl=ports,
            request=request,
            deadline_s=module.DEADLINE_S,
        )
    entry = replace(module.ENTRY, units={**module.ENTRY.units, "probe": module.Probe(condition)})
    return tk.rig_of_entry(
        directory / "run", entry, port_impl=ports, request=request, keep_units=True
    )


def answered(rig: tk.TreeRig) -> answer.TerminalAnswer:
    rig.run()
    got: answer.TerminalAnswer = tk.answer_of(rig)
    return got


# ---- the six conditions at depth two ------------------------------------------------------------


@proves_conditions
@pytest.mark.parametrize("condition", list(EXPECTED))
def test_condition_depth2_same_class(condition: str, tmp_path: Path) -> None:
    """Each inducer of `depth2_conditions`, at `stage/probe`: the same `node_class` (and code) as
    the same leaf alone at depth 0, and B4-T2's transcribed class; `never_ready` is `EXHAUSTED` with
    outcome `BLOCKED`; nothing else about the tree is a candidate."""
    outcome, klass, code = EXPECTED[condition]
    deep = answered(rig_at_depth(2, condition, tmp_path / "deep"))
    flat = answered(rig_at_depth(0, condition, tmp_path / "flat"))
    here = answer.account_of(deep, DEEP)  # the probe's own account, primary or not
    alone = answer.account_of(flat, ())
    assert here is not None and alone is not None
    assert here.node_class is not None and here.node_class.value == klass
    assert (here.node_class, here.code) == (alone.node_class, alone.code)
    if condition != "pass":  # a success is answered by the rolled-up root, which has no condition
        assert here.condition == alone.condition
    assert here.code == code
    assert deep.outcome is flat.outcome and deep.outcome.value == outcome
    if condition == "never_ready":
        assert here.node_class is NodeClass.EXHAUSTED
        assert deep.outcome is OutcomeClass.BLOCKED  # B4-T3: EXHAUSTED_AS, never `failed`
    if condition == "pass":  # a success has the root at `()` as `ROLLED_UP` `PASSED` (B4-C1)
        assert deep.primary.path == () and deep.primary.listing is Listing.ROLLED_UP
    else:  # the probe is the one candidate, so it is the primary
        assert deep.primary.path == DEEP
        assert [n for n in deep.listed if n.listing is Listing.CANDIDATE] == []


# ---- root-addressed stops with a depth-two node active ---------------------------------------


def held_tree(tmp_path: Path, gate: threading.Event, held: threading.Event) -> tk.TreeRig:
    """`app` runs `branch` and `quick`; `branch` runs `deep` (created its marker, holds in its wait)
    and `later`, which needs `deep`: so `branch/deep` is the depth-two node active at the stop, and
    the first leaf in plan order."""
    branch = tk.group("branch", (tk.bind("deep"), tk.bind("later", "deep")), budget_s=100)
    app = tk.group("app", (tk.bind("branch"), tk.bind("quick")), concurrency=2, budget_s=150)
    units: dict[str, object] = {
        "branch": branch,
        "quick": tk.leaf_unit("quick"),
        "deep": tk.held_unit("deep", gate, on_hold=lambda path: held.set()),
        "later": tk.leaf_unit("later"),
    }
    return tk.tree_rig(tmp_path, app, units)


def stopped_answer(
    rig: tk.TreeRig, cause: StopCause, held: threading.Event, gate: threading.Event
) -> answer.TerminalAnswer:
    """Run `rig`; once `held` is set raise the stop `cause`, let the leaf go, and project the
    lane with the host's stop row (B2-C15) in the fold."""
    runner = threading.Thread(target=rig.run)
    runner.start()
    try:
        assert tk.wait_for(held), "the depth-two node never held"
        rig.rig.cancel.stop = cause
        offset = rig.rig.services.attempts().committed_length()
    finally:
        gate.set()
        runner.join(tolerances.JOIN_WAIT_S)
    assert not runner.is_alive()
    folded = fold.fold_lane(rig.run_dir, rig.plan)
    name = fold.CAUSE_CANCEL if cause is StopCause.CANCEL else fold.CAUSE_RELEASE_POINT
    folded = replace(folded, stop_rows=(fold.StopRow(name, offset, "t"),))
    gone = type("Gone", (), {"confirmed_gone": True})()
    return answer.project(
        folded, CleanupDisposition(released=(sweep.GROUP_TARGET,)), gone, rig.plan, False, None
    )


def lowest_ordinal_stopped(rig: tk.TreeRig, got: answer.TerminalAnswer) -> tuple[str, ...]:
    """B4-C2's `incomplete`, computed from the plan's ordinals and the answer's own listing."""
    ordinal = {tuple(p.split("/")) if p else (): n for p, n in rig.plan.precedence_ordinal.items()}
    wanted = (Listing.STOPPED, Listing.UNENDED)
    stopped = [n.path for n in [got.primary, *got.listed] if n.listing in wanted]
    return min(stopped, key=lambda p: (ordinal[p], p))


def listing_of(got: answer.TerminalAnswer) -> dict[tuple[str, ...], Listing]:
    return {n.path: n.listing for n in [got.primary, *got.listed]}


@proves_stops
@proves_not_started
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_root_stop_depth2_active(stop: str, tmp_path: Path) -> None:
    """A cancel, or the release point, with `branch/deep` active and `branch/later` waiting on it:
    the answer's class comes from the stop, the primary is the root's own account (its class is not
    the key's), the active node is listed `STOPPED` and the waiting one `NOT_STARTED`. A release
    point also says where the run was cut: `incomplete` is the lowest-ordinal stopped path."""
    gate, held = threading.Event(), threading.Event()
    cause = StopCause.CANCEL if stop == "cancel" else StopCause.RELEASE_POINT
    got = stopped_answer(held_tree(tmp_path, gate, held), cause, held, gate)
    assert got.primary.path == () and got.primary.node_class is None  # not from the key
    listing = listing_of(got)
    assert listing[("branch", "deep")] is Listing.STOPPED
    assert listing[("branch", "later")] is Listing.NOT_STARTED
    if stop == "cancel":
        assert got.outcome is OutcomeClass.CANCELLED and got.root_stop is RootStop.CANCEL
        assert got.incomplete is None
    else:
        assert got.outcome is OutcomeClass.TIMED_OUT
        assert got.root_stop is RootStop.RELEASE_POINT
        assert got.incomplete == ("branch", "deep")


@proves_stops
@proves_not_started
def test_root_stop_depth2_active_restart(tree_kernel: Kernel, tmp_path: Path) -> None:
    """A restart with a depth-two node mid-flight (no stop row, no `NodeEnd` for it): recovery
    answers `EXECUTION_ERROR`, root stop `RESTART`, error `EXECUTION_RESTART` (rule (2)); the class
    is the restart's, not the key's; the node that had entries is listed `UNENDED` and those that
    never started `NOT_STARTED`."""
    path = tmp_path / "plugins" / "readiness_sibling.py"
    path.write_text(tk.fixture_source("readiness_sibling"), encoding="utf-8")
    from tests.proof import harness

    admitted = harness.admit_tree(path, {"env": "dev"}, kernel=tree_kernel)
    spec = json.loads((evidence_dir(admitted.run_dir) / "spec.json").read_text(encoding="utf-8"))
    from trestle.server.fold import plan_of_spec

    plan = plan_of_spec(spec)
    assert plan is not None
    run = runs.TreeRun(admitted, plan)
    RunLedger.open(ledger_path(run.run_dir)).append("started", run_id=run.run_id)
    lane = runs.lane_of(run)
    ticket = lane.issue(
        lf.Lineage(run.run_dir.name, ("branch", "w1")),
        "up",
        lf.EffectFacetClass.CREATE,
        lf.Repeat.SAFE,
        lf.Lifetime.RUN,
        lf.InRunGroup(),
        2,
        None,
    )
    lane.confirm(ticket, lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, "sel-w1"))  # type: ignore[arg-type]
    recover_run_dir(run.run_dir)  # the server died before any node ended

    view = tree_kernel.control.project.status(run.run_id)
    assert view.answer is not None  # type: ignore[union-attr]
    got = view.answer  # type: ignore[union-attr]
    assert got["outcome"] == "execution_error" and got["root_stop"] == "restart"
    assert got["recovered"] is True
    assert got["error"]["code"] == vocab.EXECUTION_RESTART
    assert got["primary"]["path"] == [] and got["primary"]["node_class"] is None
    listed = {tuple(item["path"]): item["listing"] for item in got["listed"]}
    assert listed[("branch", "w1")] == "unended"
    assert listed[("waiter",)] == listed[("branch", "w2")] == "not_started"


@proves_reached
@proves_not_started
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_root_stop_after_ordinary_failure(stop: str, tmp_path: Path) -> None:
    """`failure_dependents`: `broken` fails (an ordinary failure: only its dependents are cut, the
    root goes on, OQ-33) while `independent` is still running; then the root is stopped. The class
    is the stop's (`cancelled`, resp. `timed_out`), the primary is the root's own account, and
    `broken`'s condition is listed beside it as `failed`, never as primary; its dependents are
    `NOT_STARTED` and the running sibling `STOPPED`."""
    entry = tk.fixture_entry("failure_dependents")
    gate, held = threading.Event(), threading.Event()
    behaviour: dict[str, Any] = {
        "broken": tk.leaf_unit(
            "broken",
            advance=lambda unit, params, state, effects, ctx: Failed("fixture.broke", "on request"),
            declaration=entry.units["broken"].declare(),  # type: ignore[attr-defined]
        ),
        "independent": tk.held_unit("independent", gate, on_hold=lambda path: held.set()),
    }
    rig = tk.rig_of_entry(tmp_path, entry, behaviour=behaviour, request={"env": "dev"})
    cause = StopCause.CANCEL if stop == "cancel" else StopCause.RELEASE_POINT
    got = stopped_answer(rig, cause, held, gate)
    want = OutcomeClass.CANCELLED if stop == "cancel" else OutcomeClass.TIMED_OUT
    assert got.outcome is want, got.outcome
    assert got.primary.path == () and got.primary.node_class is None  # the root's own account
    broken = [n for n in got.listed if n.path == ("broken",)]
    assert len(broken) == 1, got.listed
    assert broken[0].listing is Listing.CANDIDATE and broken[0].node_class is NodeClass.FAILED
    assert (broken[0].condition, broken[0].code) == ("failed", "fixture.broke")
    listing = listing_of(got)
    assert listing[("left",)] is listing[("right",)] is Listing.NOT_STARTED
    assert listing[("independent",)] is Listing.STOPPED
    if want is OutcomeClass.TIMED_OUT:
        # B4-C2: the lowest-ordinal STOPPED or UNENDED vertex, whichever it is (see the RETURN's
        # G-INCOMPLETE-ROOT: the stopped root can outrank a later leaf)
        assert got.incomplete == lowest_ordinal_stopped(rig, got)


def test_the_fixture_is_the_mc_b3_01_shape() -> None:
    """Three vertices, depth three, one source line `CONDITION` (the six conditions of the plan)."""
    module = load_fixture()
    assert module.LABEL == {"vertices": 3, "depth": 3, "shared": None, "expect": "valid"}
    assert generators.measure(module.ENTRY)[:2] == (3, 3)
    assert tuple(EXPECTED) == module.CONDITIONS
    assert sum(line.startswith("CONDITION = ") for line in FIXTURE.read_text().splitlines()) == 1
