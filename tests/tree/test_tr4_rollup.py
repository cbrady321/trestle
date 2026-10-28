"""L.TR-4.5: the host answer over a tree's fold: primary by the B4-C4 key, invariance, races.

The host answer (`trestle.server.answer.project`, B4-C1, the one place the key runs, B4-I4)
decides a tree's root class and primary by B4-C2 rule (3): `primary` is the key-min candidate under
`(class rank, origin rank, precedence ordinal)`. Three families of proof, all in-library (the host
halves are `L.TR-L.10`'s):

* invariance: a single-trigger tree keeps its root class and logical primary path over every
  permutation seed and every wrapper name (B4-C4's ordinal property; judged across the whole
  `GENERATED` set by `differ d7`, L.TR-4.7, whose answer source is `tests/tree/d7_answers.py`);
* races: two triggers reached in either arrival order give the same class, primary and listing;
* the three "never" claims: `BLOCKED` is never `FAILED` (B4-I2), a repair is never a plain pass
  (B4-I3, B4-T2 row 5), cleanup is never `clean` while anything is unconfirmed (B4-C7).

The runs are the real loop over the fixtures' own declarations (`d7_answers`, MC-26's rig); the two
lane-planted cases (a repair, an unconfirmed release) write the lane as the loop would through
`AttemptLane` because no fixture behaviour grants a remedy."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.trees import generators
from tests.proof import tolerances
from tests.tree import d7_answers as d7a
from tests.tree import runs
from tests.tree import treekit as tk
from trestle.child.attempt_lane import AttemptTicket
from trestle.common import lane_format as lf
from trestle.common.outcome import OutcomeClass
from trestle.common.plan.vocabulary import Listing, NodeClass, ResourceDisposition
from trestle.server import answer, fold, sweep
from trestle.server.main import Kernel
from trestle.server.sweep import CleanupDisposition, SweepTarget
from trestle.workflow.units import Blocked, Failed, Step
from trestle.workflow.values import Resend

proves_permutation = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:permutation-invariant", "A", "tree", "LOGIC+MCP", "CI"
)
proves_completion = pytest.mark.proves(
    "WR-TERM-4", "WR-TERM-4:permuted-completion", "A", "tree", "LOGIC", "CI"
)
proves_wrapper = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:depth-invariant-wrapper-any-name", "A", "tree", "LOGIC+MCP", "CI"
)
proves_listed = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:race-conditions-listed", "A", "tree", "LOGIC+MCP", "CI"
)
proves_same = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:race-same-conditions-same-answer", "A", "tree", "LOGIC+MCP", "CI"
)
proves_blocked = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:blocked-never-failed", "A", "tree", "LOGIC+MCP", "CI"
)
proves_repair = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:repair-never-plain-pass", "A", "tree", "LOGIC+MCP", "CI"
)
proves_cleanup = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:cleanup-never-clean-unconfirmed", "A", "tree", "LOGIC+MCP", "CI"
)

SEEDS = generators.PERMUTE_SEEDS
WRAPPERS = [wrapper for _, _, wrapper in generators.WRAPPINGS]
BROKE = "fixture.broke"


def wire_of(name: str, trigger: str) -> dict[str, Any]:
    return d7a.answer_of(name, trigger)


def leaves_of(name: str) -> list[str]:
    return d7a.leaf_units(d7a.tree_named(name).entry)


# ---- invariance ---------------------------------------------------------------------------------


@proves_permutation
@pytest.mark.parametrize("seed", SEEDS)
def test_primary_invariant_under_permutation(seed: int) -> None:
    """Each leaf in turn is the one trigger: the permuted tree answers with the base's root class
    and the same primary path, the trigger's own logical path, whatever the child order."""
    permuted = f"permute_{seed}"
    base = d7a.base_of(permuted)
    assert permuted != base and leaves_of(permuted) == leaves_of(base)
    for trigger in leaves_of(base):
        want = wire_of(base, trigger)
        assert want["class"] == OutcomeClass.FAILED.value and want["primary"][-1] == trigger, want
        assert wire_of(permuted, trigger) == want, (permuted, trigger)


@proves_completion
@pytest.mark.parametrize("seed", SEEDS)
def test_permuted_tree_completes(seed: int, tmp_path: Path) -> None:
    """A permuted declaration order changes nothing about completion: every vertex of the plan has
    its end, and the run's root is answered (WR-TERM-4)."""
    permuted = f"permute_{seed}"
    entry = d7a.tree_named(permuted).entry
    rig = d7a.run_rig(entry, d7a.trigger_of(permuted, entry), tmp_path)
    ends = rig.ends()
    assert set(ends) == {"/".join(v.path.split("/")) if v.path else "" for v in rig.plan.vertices}
    assert fold.fold_lane(rig.run_dir, rig.plan).ended


@proves_wrapper
@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_primary_invariant_wrapped_deeper(wrapper: str) -> None:
    """One node wrapped in one more composite, under a name that sorts before or after its
    siblings, or between them: the root class and the logical primary path are unchanged; the
    wrapped node's own path gains exactly the wrapper's segment, and no other node's does."""
    base, node, _ = next(w for w in generators.WRAPPINGS if w[2] == wrapper)
    wrapped = f"{base}__{wrapper}"
    assert leaves_of(wrapped) == leaves_of(base)
    for trigger in leaves_of(base):
        want = wire_of(base, trigger)
        got = wire_of(wrapped, trigger)
        assert got["class"] == want["class"] == OutcomeClass.FAILED.value, trigger
        if trigger == node:
            assert got["primary"] == [*want["primary"][:-1], wrapper, node], got
        else:
            assert got["primary"] == want["primary"], (trigger, got, want)
        assert [seg for seg in got["primary"] if seg != wrapper] == want["primary"], trigger


def test_d7_default_answers_are_the_product_answers() -> None:
    """`differ d7` over the product's tree answers exits 0 (L.TR-4.7 built the mode, this leaf
    supplies the source): every permutation and wrapping of `GENERATED` agrees with its base."""
    done = subprocess.run(
        [sys.executable, "-m", "tests.proof.differ", "d7"],
        capture_output=True,
        text=True,
        timeout=tolerances.JOIN_WAIT_S * 6,
        check=False,
        cwd=tk.REPO,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "0 diffs" in done.stdout


# ---- races ---------------------------------------------------------------------------------------


def _ended(rig: tk.TreeRig, path: str) -> bool:
    """Whether `path`'s end is on the lane, read without asserting on a half-written last line."""
    return any(row.cls == "end" and row.path == path for row in rig.rig.lane().rows)


def _wait_until_ended(holder: list[tk.TreeRig], path: str) -> None:
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < deadline:
        if holder and _ended(holder[0], path):
            return
        time.sleep(tolerances.POLL_FINE_S)
    raise AssertionError(f"{path} never ended")


def _ending(step: Step, after: str | None, holder: list[tk.TreeRig]) -> Callable[..., Step]:
    """A leaf's `advance`: wait until `after` has ended (when given), then end with `step`."""

    def advance(unit: Any, params: Any, state: Any, effects: Any, ctx: Any) -> Step:
        if after is not None:
            _wait_until_ended(holder, after)
        return step

    return advance


def race(
    tmp_path: Path, left: Step, right: Step, order: tuple[str, str]
) -> tuple[answer.TerminalAnswer, list[str]]:
    """`race_two_trigger` with `left` and `right` each ending with the given step, the leaf named
    second in `order` ending only once the first has (the fake schedule); the answer and the order
    the two `NodeEnd`s were written."""
    entry = tk.fixture_entry("race_two_trigger")
    first, second = order
    holder: list[tk.TreeRig] = []
    steps = {"left": left, "right": right}
    behaviour = {
        name: tk.leaf_unit(
            name,
            advance=_ending(steps[name], first if name == second else None, holder),
            declaration=entry.units[name].declare(),  # type: ignore[attr-defined]
        )
        for name in ("left", "right")
    }
    rig = tk.rig_of_entry(tmp_path, entry, behaviour=behaviour)
    holder.append(rig)
    rig.run()
    written = [p for p in (row["path"] for row in rig.rows() if row["class"] == "end") if p]
    return tk.answer_of(rig), written


def broke() -> Step:
    return Failed(BROKE, "a trigger")


def blocked() -> Step:
    return Blocked("fixture.blocked", "Fix the fixture, then re-send.", Resend.WILL_NOT_SUCCEED)


def reached(got: answer.TerminalAnswer) -> list[tuple[tuple[str, ...], str | None]]:
    """Every node that reached a condition, primary first: (path, class)."""
    nodes = [got.primary, *got.listed]
    return [
        (n.path, n.node_class.value if n.node_class else None)
        for n in nodes
        if n.listing is Listing.CANDIDATE
    ]


@proves_listed
@pytest.mark.parametrize("pair", [("failed", "blocked"), ("failed", "failed")])
def test_two_trigger_race_lists_reached_conditions(tmp_path: Path, pair: tuple[str, str]) -> None:
    """Both triggers of `race_two_trigger` are reached before the run ends (a failed branch does not
    stop the root, OQ-33): one is the primary, the other is listed with its own class, so no
    reached condition is lost. The primary is the key-min: the higher class rank whichever ended
    first, else the earlier ordinal."""
    steps = {"failed": broke, "blocked": blocked}
    got, _ = race(tmp_path, steps[pair[0]](), steps[pair[1]](), ("left", "right"))
    assert got.primary.listing is Listing.CANDIDATE
    conditions = reached(got)
    assert sorted(path for path, _ in conditions) == [("left",), ("right",)], conditions
    assert [c for c in conditions if c[0] == got.primary.path] == [conditions[0]]  # primary first
    classes = dict(conditions)
    assert classes[("left",)] == pair[0] and classes[("right",)] == pair[1]
    assert got.primary.path == ("left",)  # rank failed < blocked, and on a tie the earlier ordinal
    assert got.outcome is OutcomeClass.FAILED


@proves_same
@pytest.mark.parametrize(
    "pair", [("failed", "blocked"), ("blocked", "failed"), ("failed", "failed")]
)
def test_race_same_conditions_same_class_primary(tmp_path: Path, pair: tuple[str, str]) -> None:
    """Two runs whose nodes reached the same conditions, forced by the schedule to reach them in
    opposite orders, give the same root class, primary and listing."""
    steps = {"failed": broke, "blocked": blocked}
    one, order_one = race(tmp_path / "one", steps[pair[0]](), steps[pair[1]](), ("left", "right"))
    two, order_two = race(tmp_path / "two", steps[pair[0]](), steps[pair[1]](), ("right", "left"))
    assert order_one[:2] == ["left", "right"], order_one
    assert order_two[:2] == ["right", "left"], order_two  # the schedule did reverse the arrival
    assert (one.outcome, one.primary, one.listed) == (two.outcome, two.primary, two.listed)
    assert reached(one) == reached(two)


# ---- never FAILED, never a plain pass, never clean ------------------------------------------


@proves_blocked
def test_blocked_never_failed(tmp_path: Path) -> None:
    """A node that ends `BLOCKED` is answered blocked: its own class is `BLOCKED`, the root class
    is `BLOCKED` when it is the primary, and nothing in the answer says `FAILED` for it, alone or
    beside a failed sibling (B4-I2)."""
    alone, _ = race(tmp_path / "alone", blocked(), Failed(BROKE, "x"), ("left", "right"))
    # left blocked, right failed: the failed node outranks the blocked one, and the blocked node
    # is still listed as blocked, never as failed
    classes = dict(reached(alone))
    assert classes[("left",)] == NodeClass.BLOCKED.value
    assert classes[("right",)] == NodeClass.FAILED.value
    assert alone.outcome is OutcomeClass.FAILED and alone.primary.path == ("right",)

    both = tk.rig_of_entry(
        tmp_path / "solo",
        tk.fixture_entry("two_branch_barrier"),
        behaviour={
            "left": tk.leaf_unit(
                "left",
                advance=_ending(blocked(), None, []),
                declaration=tk.fixture_entry("two_branch_barrier").units["left"].declare(),  # type: ignore[attr-defined]
            )
        },
    )
    both.run()
    got = tk.answer_of(both)
    assert got.outcome is OutcomeClass.BLOCKED
    assert got.primary.path == ("left",) and got.primary.node_class is NodeClass.BLOCKED
    assert got.primary.human_action and got.primary.resend  # the human action, byte for byte
    every = [got.primary, *got.listed]
    assert not [n for n in every if n.node_class is NodeClass.FAILED]
    # the dependent of the blocked node is not started, and is said to be so
    listing = {n.path: n.listing for n in every}
    assert listing[("join",)] is Listing.NOT_STARTED


def _planted(kernel: Kernel, name: str) -> runs.TreeRun:
    return runs.admit(kernel, generators.fixture_tree(name))


def _project(run: runs.TreeRun, cleanup: CleanupDisposition) -> answer.TerminalAnswer:
    folded = fold.fold_lane(run.run_dir, run.plan)
    gone = type("Gone", (), {"confirmed_gone": True})()
    return answer.project(folded, cleanup, gone, run.plan, False, None)


def _issue(run: runs.TreeRun, lane: Any, path: tuple[str, ...], grant: Any = None) -> Any:
    return lane.issue(
        lf.Lineage(run.run_dir.name, path),
        "up",
        lf.EffectFacetClass.CREATE,
        lf.Repeat.SAFE,
        lf.Lifetime.RUN,
        lf.InRunGroup(),
        2,
        grant,
    )


CLEAN = CleanupDisposition(released=(sweep.GROUP_TARGET,))


@proves_repair
def test_repair_never_plain_pass(tree_kernel: Kernel) -> None:
    """A node that was repaired (a confirmed applied remedy, B4-T2 row 5) is `REPAIRED`, never
    `PASSED`, and its resource disposition is `REPAIRED`, not clean: the same tree without the
    repair is a plain pass, so the difference is the remedy alone."""
    plain = _planted(tree_kernel, "two_branch_barrier")
    runs.write_all_ends(plain, runs.lane_of(plain))
    unrepaired = _project(plain, CLEAN)
    assert unrepaired.outcome is OutcomeClass.PASSED
    assert unrepaired.primary.node_class is NodeClass.PASSED

    fixed = runs.admit(
        tree_kernel, generators.fixture_tree("race_two_trigger")
    )  # a second tree, so the two runs are independent
    lane = runs.lane_of(fixed)
    ticket = _issue(fixed, lane, ("left",), lf.RemedyGrant("code", "up", 1))
    assert isinstance(ticket, AttemptTicket), ticket
    lane.confirm(ticket, lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, "sel-left"))
    runs.write_all_ends(fixed, lane)
    got = _project(fixed, CLEAN)
    assert got.primary.path == ("left",)
    assert got.primary.node_class is NodeClass.REPAIRED
    assert got.primary.disposition is ResourceDisposition.REPAIRED
    assert (got.primary.condition, got.primary.code) == ("satisfied", None)  # it did satisfy


@proves_cleanup
def test_cleanup_never_clean_unconfirmed(tree_kernel: Kernel) -> None:
    """A tree whose created resource K is not confirmed gone is not answered clean: the fake
    inventory (the sweep) reports K's release `unknown`, and `unknown` dominates any other
    disposition. The same tree with K's release recorded is clean."""
    released = _planted(tree_kernel, "two_branch_barrier")
    lane = runs.lane_of(released)
    ticket = _issue(released, lane, ("left",))
    handle = lane.confirm(ticket, lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, "sel-k"))
    assert handle is not None
    runs.write_all_ends(released, lane)
    lane.record_released(handle, "stopped")
    ok = _project(released, CLEAN)
    # the run group and K are the two released targets
    assert ok.cleanup.clean and ok.cleanup.released == 2 and ok.cleanup.unknown == 0

    open_ = _planted(tree_kernel, "two_branch_barrier")
    lane = runs.lane_of(open_)
    ticket = _issue(open_, lane, ("left",))
    lane.confirm(ticket, lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, "sel-k"))
    runs.write_all_ends(open_, lane)
    unknown = CleanupDisposition(
        released=(sweep.GROUP_TARGET,), unknown=(SweepTarget(("left",), "up"),)
    )
    got = _project(open_, unknown)
    assert not got.cleanup.clean and got.cleanup.unknown == 1
    assert got.primary.node_class is NodeClass.PASSED  # the nodes passed; only cleanup is not clean
    # K is released only when its release is recorded or the sweep says so: with neither, only
    # the run group counts (nothing is claimed for K)
    silent = _project(open_, CLEAN)
    assert silent.cleanup.released == 1 and silent.cleanup.unknown == 0
    assert got.cleanup.released == 1  # K's `unknown` replaced any other word for it
