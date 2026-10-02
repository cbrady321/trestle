"""L.TR-5.1: the selection pass over a ChoiceNode, with fake realizations (V-7.2, B1-O4, B1-I8).

`choice_fake` (MC-B3-01) is a root that chooses between `fake_a` and `fake_b`. Each case runs the
real loop over the real lane (MC-26's rig, a manual clock) with a scripted leaf per alternative
whose marker is present or absent as the case says, so the four properties are read off the
record and the evidence, never assumed:

* every eligible alternative is observed (read-only) before the plan identity is recorded, and the
  plan identity, which carries the selection, is the first lane entry, before any ticket;
* the same inputs give the same selection and the same observation digest;
* selection stays in the declared space: nothing a unit returns adds a vertex, an alternative the
  selection left out has no `NodeEnd`, and every path the lane holds is a vertex of the plan.

A ChoiceNode root is still refused at admission until L.TR-5.3, so these are library cases: the
admission-refusal test is not touched here."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.workflow.declarations import LeafDeclaration
from trestle.workflow.units import ObserveContext, ReadFacets
from trestle.workflow.values import Observation

proves = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:selection-validated-only", "A", "tree", "LOGIC", "CI"
)

ALTERNATIVES = ("fake_a", "fake_b")


def _rig(
    tmp_path: Path,
    *,
    present: tuple[str, ...] = (),
    request: dict[str, Any] | None = None,
    entry_edits: dict[str, str] | None = None,
    payload: Any = None,
) -> tk.TreeRig:
    """`choice_fake` admitted, each alternative a scripted leaf over its declaration; the markers
    of `present` exist before the run (an instance of that realization is up)."""
    entry = tk.fixture_entry("choice_fake", entry_edits)
    marker = tk.PathMarker()
    marker._live.update(present)  # noqa: SLF001 (the rig's own state: the machine before the run)
    behaviour = {
        name: _alternative(name, entry.units[name].declare(), payload)  # type: ignore[attr-defined]
        for name in ALTERNATIVES
    }
    return tk.rig_of_entry(tmp_path, entry, marker, behaviour=behaviour, request=request or {})


def _alternative(name: str, declared: LeafDeclaration, payload: Any) -> kit.Unit:
    """A scripted leaf over `declared`; with a `payload`, its observations carry it (what a unit
    reports about the world is data, never a new vertex)."""
    base = tk.leaf_unit(name, declaration=declared)

    def observe(unit: kit.Unit, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        seen = base.observe(params, reads, ctx)
        return seen if payload is None else replace(seen, payload=payload)

    return kit.Unit(base.decl, observe, base._advance, base._release)  # noqa: SLF001


def _plan_entry(rig: tk.TreeRig) -> dict[str, Any]:
    plans = [row for row in rig.rows() if row["class"] == "plan"]
    assert len(plans) == 1, "record_plan is written exactly once (V-4.8)"
    return plans[0]


def _selected(rig: tk.TreeRig) -> dict[str, str]:
    return dict(_plan_entry(rig)["selection"])


@proves
def test_all_eligible_alternatives_observed_before_first_ticket(tmp_path: Path) -> None:
    """Both alternatives are observed, read-only, and the observations, the selection and the plan
    identity all precede the first ticket anywhere in the root (B1-I8)."""
    rig = _rig(tmp_path)
    created_after_plan: list[bool] = []

    def on_create(path: str) -> None:  # runs inside the create call, after its ticket is durable
        created_after_plan.append(any(r["class"] == "plan" for r in rig.rows()))

    rig.marker.on_create = on_create
    rig.run()

    observed = [
        (fields["path"], fields["phase"])
        for kind, fields in rig.rig.sink.events
        if kind == "step.observed" and fields.get("phase") == "selection"
    ]
    assert observed == [("fake_a", "selection"), ("fake_b", "selection")]  # declared order, all
    kinds = rig.rig.sink.kinds()
    assert kinds.index("plan.identity") > max(
        n for n, (kind, f) in enumerate(rig.rig.sink.events) if f.get("phase") == "selection"
    )
    assert rig.marker.calls[:2] == [
        ("observe", "fake_a"),
        ("observe", "fake_b"),
    ]  # read-only, first
    assert created_after_plan and all(created_after_plan)  # every create followed the plan entry
    rows = rig.rows()
    classes = [row["class"] for row in rows]
    assert classes[0] == "plan"
    assert classes.index("issue") > classes.index("plan")


@proves
def test_selection_deterministic(tmp_path: Path) -> None:
    """Same inputs, same choice and the same observation digest; the choice follows V-7.2: the
    first eligible alternative whose instance is present, else the fallback, else the first
    eligible; `select_arg` restricts eligibility."""
    cases: list[tuple[str, tuple[str, ...], dict[str, Any], dict[str, str], str]] = [
        ("nothing up: the fallback", (), {}, {}, "fake_a"),
        ("only b up", ("fake_b",), {}, {}, "fake_b"),
        ("both up: declared order", ("fake_a", "fake_b"), {}, {}, "fake_a"),
        (
            "argument names b, nothing up: the first eligible (the fallback is not eligible)",
            (),
            {"realization": "fake_b"},
            {"select_arg=None": 'select_arg="realization"'},
            "fake_b",
        ),
        (
            "argument names a, b is up: b is not eligible",
            ("fake_b",),
            {"realization": "fake_a"},
            {"select_arg=None": 'select_arg="realization"'},
            "fake_a",
        ),
    ]
    for n, (label, present, request, edits, expected) in enumerate(cases):
        digests = []
        for repeat in range(2):
            rig = _rig(
                tmp_path / f"c{n}r{repeat}", present=present, request=request, entry_edits=edits
            )
            rig.run()
            assert _selected(rig) == {"": expected}, label
            digests.append(_plan_entry(rig)["observations_digest"])
        assert digests[0] == digests[1], label
    # the digest names what the selection used: a different machine state gives a different one
    up_a = _rig(tmp_path / "da", present=("fake_a",))
    up_a.run()
    up_b = _rig(tmp_path / "db", present=("fake_b",))
    up_b.run()
    assert _plan_entry(up_a)["observations_digest"] != _plan_entry(up_b)["observations_digest"]


@proves
def test_plan_identity_recorded_before_first_ticket(tmp_path: Path) -> None:
    """The plan entry is the lane's first entry and holds the selection vector; the selected
    alternative's ticket and the root's `NodeEnd`s follow it, and the one plan entry is written
    on a stop before the selection too (with the selection made so far: none)."""
    rig = _rig(tmp_path / "run")  # nothing up: the fallback is selected and created
    rig.run()
    rows = rig.rows()
    assert rows[0]["class"] == "plan"
    assert rows[0]["selection"] == {"": "fake_a"}
    issues = [n for n, row in enumerate(rows) if row["class"] == "issue"]
    ends = [n for n, row in enumerate(rows) if row["class"] == "end"]
    assert issues and ends and all(n > 0 for n in issues + ends)
    # no ticket for the alternative the selection left out (it was only ever observed)
    assert {rows[n]["path"] for n in issues} == {"fake_a"}

    stopped = _tampered(tmp_path / "tampered")
    stopped.run()
    assert _plan_entry(stopped)["selection"] == {}  # a stop before the selection records none
    assert not [r for r in stopped.rows() if r["class"] == "issue"]
    assert stopped.marker.calls == []  # not even an observation: the stop precedes the phase


def _tampered(where: Path) -> tk.TreeRig:
    """A choice tree whose loop is shown a plan with the wrong digest: the root stops before its
    selection phase (B1-E7)."""
    from dataclasses import replace

    def shown(plan: Any) -> Any:
        return replace(plan, plan_digest="0" * 64)

    entry = tk.fixture_entry("choice_fake")
    behaviour = {
        name: tk.leaf_unit(name, declaration=entry.units[name].declare())  # type: ignore[attr-defined]
        for name in ALTERNATIVES
    }
    root = entry.units[entry.root]
    return tk.tree_rig(
        where,
        root,  # type: ignore[arg-type]
        behaviour,
        deadline_s=entry.deadline.total_seconds(),
        shown=shown,
    )


@proves
def test_no_node_added_from_runtime_result(tmp_path: Path) -> None:
    """A unit that reports another realization in its observation payload adds nothing: the
    selection names a declared alternative, the walked set is the plan minus the alternative the
    selection left out, and every path the lane holds is a vertex of the admitted plan (V-7.1,
    V-4.8)."""
    rig = _rig(tmp_path, present=("fake_b",), payload={"realization": "fake_c", "path": "fake_c"})
    rig.run()
    plan_paths = {v.path for v in rig.plan.vertices}
    assert set(_selected(rig).values()) <= set(rig.plan.eligible[""])
    assert {row["path"] for row in rig.rows() if "path" in row} <= plan_paths
    ends = rig.ends()
    assert set(ends) == plan_paths - {"fake_a"}  # V_run: the unselected alternative is not walked
    assert ends["fake_b"]["condition"] == "satisfied"
    assert ends[""]["condition"] is None and ends[""]["cut"] is None  # the choice rolled up
    assert "fake_a" not in rig.marker.paths("create")  # nothing of the left-out one ran
