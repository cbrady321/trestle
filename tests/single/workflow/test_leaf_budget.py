"""L.SL-2.1: in-node stage budgets are refused at publication, and cleanup is not cut off.

A leaf's stages (its wait, its remedies and the release timeout of what it creates) must fit the
budget it declares (`publication.budget_exceeds_leaf`, refused by `check_declaration` before any
snapshot exists, so no run id, no lane and no effect can follow). A leaf that fits is untouched.
The second half is the run-time side of the same rule: a run stopped at its release point walks
its release inside the release slice and the run group is never signalled (the child exits by
itself), and the release finishes before the deadline itself (B2-C10, MC-09)."""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import harness, records
from tests.single.contract import declared_fixtures as fx
from trestle.common import clock
from trestle.common.plan import vocabulary as vocab
from trestle.common.types import PublishView, RequestOutcome
from trestle.server.main import create_kernel
from trestle.workflow import (
    EffectDeclaration,
    EffectFacetClass,
    Lifetime,
    RemedyDeclaration,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.extract import ExtractionRefused, extract_declared_tree
from trestle.workflow.registration import check_declaration, stage_budget_s

REPO = Path(__file__).resolve().parents[3]
SPINE_FIXTURE = REPO / "tests" / "fixtures" / "workflows" / "spine_leaf.py"
DECLARED_DEADLINE_S = 120  # the fixture's own (decorator and entry)
SHORT_DEADLINE_S = 20  # release slice 10 s + leaf budget 8 s fits; the wait starts late (`stall`)

SOURCE = """
from __future__ import annotations

from datetime import timedelta

from trestle.plugin import Context, trestle
from trestle.workflow import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)

CREATE = EffectDeclaration(
    effect="up",
    facet=EffectFacetClass.CREATE,
    verb="",
    lifetime=Lifetime.RUN,
    host_sections=frozenset(),
    release_timeout=timedelta(seconds=RELEASE_S),
)
FIELDS = dict(
    unit="unit",
    flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
    preconditions=(),
    postcondition="ready",
    wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=WAIT_S)),
    resource_kind="marker",
    may_touch=frozenset({"marker"}),
    effects=(CREATE,),
    retryable=frozenset(),
    remedies=(),
    budget=timedelta(seconds=BUDGET_S),
    max_attempts=1,
)


class Unit:
    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(**FIELDS)


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=300))


@trestle(deadline=300)
def wf(ctx: Context, name: str = "x") -> dict[str, str]:
    return {"name": name}
"""


def _source(*, wait_s: int, release_s: int, budget_s: int, name: str = "wf") -> str:
    text = (
        SOURCE.replace("WAIT_S", str(wait_s))
        .replace("RELEASE_S", str(release_s))
        .replace("BUDGET_S", str(budget_s))
    )
    return text.replace("def wf(", f"def {name}(")


def _leaf(**changes: Any) -> Any:
    return dataclasses.replace(fx.leaf_declaration(), **changes)


def _entry(decl: Any) -> WorkflowEntry:
    return WorkflowEntry(root=decl.unit, units={decl.unit: decl}, deadline=timedelta(seconds=300))


def _seconds(n: float) -> timedelta:
    return timedelta(seconds=n)


# ---- the refusal ------------------------------------------------------------------------------


def test_stage_budget_is_wait_plus_remedies_plus_longest_release_timeout() -> None:
    decl = fx.leaf_declaration()  # wait 60 s, one 20 s remedy, one 30 s release timeout
    assert stage_budget_s(decl) == 60 + 20 + 30
    two_creates = (
        *decl.effects,
        dataclasses.replace(decl.effects[0], effect="other", release_timeout=_seconds(5)),
    )
    two_remedies = (
        *decl.remedies,
        RemedyDeclaration("net.flaky", "create_svc", 1, _seconds(7), _seconds(1)),
    )
    both = dataclasses.replace(decl, effects=two_creates, remedies=two_remedies)
    assert stage_budget_s(both) == 60 + (20 + 7) + 30  # remedies add up, releases take the longest
    durable = dataclasses.replace(decl.effects[0], lifetime=Lifetime.DURABLE, release_timeout=None)
    assert stage_budget_s(dataclasses.replace(decl, effects=(durable,), remedies=())) == 60


@pytest.mark.proves("WR-DEADLINE-3", "WR-DEADLINE-3:single-vertex", "A", "single", "LOGIC", "CI")
def test_over_budget_leaf_refused_before_mutation(tmp_path: Path) -> None:
    """The refusal is at publication: the code is stable, it names the budget, no snapshot is
    promoted, the plugin is not in the catalog and admission has no run to make for it."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = create_kernel(home=tmp_path / "trestle", plugin_dirs=[plugin_dir], skip_recovery=True)
    fits = kernel.control.publish_plugin(_source(wait_s=20, release_s=2, budget_s=30))
    assert isinstance(fits, PublishView)  # 22 s of stages in a 30 s budget
    snapshots = tmp_path / "trestle" / "snapshots"
    before = sorted(p.name for p in snapshots.iterdir())

    over = kernel.control.publish_plugin(_source(wait_s=29, release_s=2, budget_s=30, name="over"))
    assert isinstance(over, RequestOutcome)
    assert over.code == vocab.BUDGET_EXCEEDS_LEAF == "publication.budget_exceeds_leaf"
    assert over.origin == "publication" and not over.retryable
    assert "budget" in over.message and "31s" in over.message and "30s" in over.message
    assert sorted(p.name for p in snapshots.iterdir()) == before  # nothing promoted
    assert kernel.registry.get("over") is None  # nothing to run: no claim, no run id
    runs = tmp_path / "trestle" / "runs"
    assert not runs.exists() or not any(runs.rglob("lane.ndjson"))
    assert kernel.registry.get("wf") is not None  # the fitting leaf is unchanged


def _over_by_one_stage() -> dict[str, Any]:
    """The 110 s fixture leaf with one stage lengthened past its 110 s budget."""
    base = fx.leaf_declaration()
    return {
        "wait": dataclasses.replace(base, wait=WaitPolicy(_seconds(2), 1.5, _seconds(61))),
        "remedy": dataclasses.replace(
            base,
            remedies=(RemedyDeclaration("tool.busy", "create_svc", 2, _seconds(21), _seconds(1)),),
        ),
        "release_timeout": dataclasses.replace(
            base, effects=(dataclasses.replace(base.effects[0], release_timeout=_seconds(31)),)
        ),
    }


@pytest.mark.parametrize("stage", ["wait", "remedy", "release_timeout"])
def test_each_stage_counts_and_equality_fits(stage: str) -> None:
    base = dataclasses.replace(fx.leaf_declaration(), budget=_seconds(110))  # stages: 110 s
    assert check_declaration(base) == ()  # equality fits
    over = dataclasses.replace(_over_by_one_stage()[stage], budget=_seconds(110))
    (refusal,) = check_declaration(over)
    assert refusal.code == vocab.BUDGET_EXCEEDS_LEAF and refusal.element == "budget"
    with pytest.raises(ExtractionRefused) as extraction:
        extract_declared_tree(_entry(over))
    assert extraction.value.code == vocab.BUDGET_EXCEEDS_LEAF
    assert check_declaration(dataclasses.replace(over, budget=_seconds(112))) == ()


def test_fitting_leaves_unchanged_and_unusable_stage_values_are_not_this_rules() -> None:
    fitting = fx.leaf_declaration()
    assert check_declaration(fitting) == ()
    assert extract_declared_tree(fx.leaf_entry()).root == "svc"
    # an unusable stage value is another rule's refusal (the six elements), never a second one
    broken = _leaf(wait=None)
    codes = [r.code for r in check_declaration(broken)]
    assert codes == [vocab.PLAN_CONTRACT_MISSING]
    unbudgeted = _leaf(budget=None)
    assert check_declaration(unbudgeted) == ()  # nothing to compare with: not this rule's ground
    no_release = _leaf(
        effects=(
            EffectDeclaration("up", EffectFacetClass.CREATE, "", Lifetime.RUN, frozenset(), None),
        ),
        remedies=(),
    )
    assert [r.code for r in check_declaration(no_release)] == [vocab.RELEASE_TIMEOUT_MISSING]


def test_composite_roots_are_not_budget_checked_here() -> None:
    """A-1 checks one leaf; a composite's own bounds are the plan's (B2-C2), not registration's."""
    assert check_declaration(fx.all_declaration()) == ()
    assert check_declaration(fx.choice_declaration()) == ()


# ---- cleanup is not cut off at the deadline ---------------------------------------------------


class SignalSpy:
    """Every os.kill / os.killpg this process makes, with the time it was made."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, str, int]] = []
        self._patch = pytest.MonkeyPatch()
        self._lock = threading.Lock()

    def __enter__(self) -> SignalSpy:
        real_kill, real_killpg = os.kill, os.killpg

        def kill(pid: int, signum: int) -> None:
            with self._lock:
                self.calls.append((time.time(), "kill", int(signum)))
            real_kill(pid, signum)

        def killpg(pgid: int, signum: int) -> None:
            with self._lock:
                self.calls.append((time.time(), "killpg", int(signum)))
            real_killpg(pgid, signum)

        self._patch.setattr(os, "kill", kill)
        self._patch.setattr(os, "killpg", killpg)
        return self

    def __exit__(self, *exc: object) -> None:
        self._patch.undo()


@contextmanager
def _signals() -> Iterator[SignalSpy]:
    with SignalSpy() as spy:
        yield spy


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_cleanup_completes_before_deadline_kill(tmp_path: Path) -> None:
    """A run stopped at its release point (the deadline less the release slice) while it waits:
    its release walk (the stop issue, its confirmation, `released`) is finished inside the release
    slice, before the deadline itself, and before anything is signalled: the child exits by
    itself, so the run group is confirmed gone by `exit` and `os.kill`/`os.killpg` are never
    called. The kill is the fallback for a walk that outlasts the slice (SV-3.6)."""
    source = SPINE_FIXTURE.read_text(encoding="utf-8")
    source = source.replace(f"deadline={DECLARED_DEADLINE_S}", f"deadline={SHORT_DEADLINE_S}")
    source = source.replace(f"seconds={DECLARED_DEADLINE_S}", f"seconds={SHORT_DEADLINE_S}")
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    fixture = plugin_dir / SPINE_FIXTURE.name
    fixture.write_text(source, encoding="utf-8")
    with _signals() as spy:
        admitted = harness.admit_tree(fixture, {"env": "dev", "mode": "stall"})
        with support.reaping(admitted.run_id):
            view = harness.drive_tree(admitted)
    run_dir = admitted.run_dir
    wire = view.to_dict()
    assert wire["outcome"]["class"] == "timed_out", wire["outcome"]

    ledger = records.ledger_rows(run_dir).rows
    (stop,) = [r for r in ledger if r.get("kind") == "stop_row"]
    assert stop["cause"] == "release_point"
    (group_stop,) = [r for r in ledger if r.get("kind") == "group_stop"]
    assert group_stop["confirmed_gone"] is True and group_stop["method"] == "exit", group_stop
    assert spy.calls == [], f"the run group was signalled: {spy.calls}"

    lane = records.lane_rows(run_dir)
    walk = [r for r in lane.rows if r.entry.get("effect") == "stop" or r.cls == "released"]
    assert [r.cls for r in walk] == ["issue", "confirmation", "released"], walk
    assert walk[1].entry["status"] == "applied"
    released_at = _iso(walk[2].entry["released_at"])

    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    deadline = _iso(spec["deadline"])
    release_point = deadline - timedelta(seconds=spec["plan"]["release_slice"])
    assert spec["plan"]["release_slice"] == clock.release_slice
    # the stop row's `at` has whole-second resolution, so it can sit up to a second early
    assert release_point - timedelta(seconds=1) <= _iso(stop["at"]) <= deadline
    assert released_at <= _iso(stop["at"]) + timedelta(seconds=clock.release_slice + 1)
    assert released_at < deadline  # the cleanup is finished before the deadline, not cut off by it
