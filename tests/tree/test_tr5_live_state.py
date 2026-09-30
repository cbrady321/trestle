"""L.TR-5.2: live-state conditions stop the dependent node before its effect, in one class, coded.

`live_state` (MC-B3-01) runs one condition per `case`, every other node healthy. Each case runs the
real loop over the real lane (MC-26's rig, a manual clock) with the fixture's own units and its
`FakeMarker` ports, and is read off the record:

* an externally managed instance that is not running (V-7.4): the unit's `Blocked`
  `REALIZATION_ABSENT`, its dependent never started;
* a live definition that names a node outside the declaration (B1-E7): the gate is not satisfied
  and the root stops `DECLARATION_STALE`, every vertex `NOT_STARTED`, no ticket anywhere;
* a selected alternative that is not reachable from the dependent's vantage (V-7.3): the dependent
  is `BLOCKED` `ROUTE_UNSUPPORTED` with its human action before the first ticket in the root;
* a toolchain that is missing (V-11.2, J-5a): the create is `NOT_APPLIED` `TOOLCHAIN_MISSING` and
  the node `BLOCKED`, the tool named in its human action; nothing was started.

For each: the vertex that carries the code ends with it, no vertex the condition stops has a ticket
after it, and B4-T2 gives exactly one vertex a class other than `PASSED` (row 9 `BLOCKED` for an
absent instance, an infeasible route and a missing toolchain; row 3 `EXECUTION_ERROR` for a stale
declaration)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker

from tests.fixtures.trees import live_state
from tests.tree import treekit as tk
from trestle.common.plan import precedence
from trestle.workflow import ports

proves = pytest.mark.proves("WR-UNIT-8", "WR-UNIT-8:live-state-stops", "A", "tree", "LOGIC", "CI")

CASES = ("absent_em_instance", "drifted_definition", "infeasible_route", "missing_toolchain")


@pytest.fixture
def markers(tmp_path: Path) -> Iterator[FakeMarker]:
    marker = FakeMarker(tmp_path / "markers", "run")
    yield marker
    marker.close()


def _run(
    tmp_path: Path, marker: FakeMarker, case: str, port_map: dict[type, object] | None = None
) -> tk.TreeRig:
    request = {"env": "dev", "case": case}
    rig = tk.rig_of_entry(
        tmp_path / "run",
        live_state.ENTRY,
        keep_units=True,
        port_impl=port_map if port_map is not None else live_state.prepare(marker, case),
        request=request,
    )
    rig.run()
    return rig


def _classes(rig: tk.TreeRig) -> dict[str, str]:
    """B4-T2 over every vertex that is a candidate (it reached a condition itself): its class."""
    out: dict[str, str] = {}
    for path, end in rig.ends().items():
        like = SimpleNamespace(
            condition=end["condition"],
            code=end["code"],
            cut=end["cut"],
            provenance=end["provenance"],
        )
        klass = precedence.node_class(like)
        if klass is not None:
            out[path] = klass.value
    return out


def _issues(rig: tk.TreeRig, path: str) -> list[dict[str, Any]]:
    return [r for r in rig.rows() if r["class"] == "issue" and r["path"] == path]


def _not_passed(rig: tk.TreeRig) -> dict[str, str]:
    return {p: c for p, c in _classes(rig).items() if c != "passed"}


def test_healthy_case_injects_nothing(tmp_path: Path, markers: FakeMarker) -> None:
    """The control: with no condition every node converges and every class is `passed`."""
    rig = _run(tmp_path, markers, "healthy")
    assert _not_passed(rig) == {}
    ends = rig.ends()
    assert all(e["cut"] is None for e in ends.values())
    assert ends["client"]["condition"] == "satisfied"
    assert ends["gateway"]["condition"] == "satisfied"


@proves
@pytest.mark.parametrize("case", CASES)
def test_live_state_condition(case: str, tmp_path: Path, markers: FakeMarker) -> None:
    rig = _run(tmp_path, markers, case)
    ends = rig.ends()
    not_passed = _not_passed(rig)
    if case == "absent_em_instance":
        carrier = ends["data/managed_data"]
        assert (carrier["condition"], carrier["code"]) == ("blocked", live_state.ABSENT_CODE)
        assert carrier["human_action"] == live_state.ABSENT_ACTION
        assert carrier["cut"] is None
        assert not _issues(
            rig, "data/managed_data"
        )  # an externally managed alternative has no effect
        assert (ends["client"]["condition"], ends["client"]["cut"]) == (None, "not_started")
        assert not _issues(rig, "client")
        assert not_passed == {"data/managed_data": "blocked"}  # row 9, by condition
    elif case == "drifted_definition":
        root = ends[""]
        assert (root["condition"], root["code"], root["cut"]) == (
            "failed",
            "execution.declaration_stale",
            None,
        )
        assert all(e["cut"] == "not_started" for path, e in ends.items() if path != "")
        assert [r for r in rig.rows() if r["class"] == "issue"] == []  # not one ticket, anywhere
        assert not_passed == {"": "execution_error"}  # row 3
    elif case == "infeasible_route":
        carrier = ends["gateway"]
        assert (carrier["condition"], carrier["code"]) == ("blocked", "admission.route_unsupported")
        assert carrier["human_action"].startswith("Select a realization of gateway reachable")
        assert carrier["resend"] == "succeeds_after_action"
        assert carrier["cut"] is None
        assert not _issues(rig, "gateway")
        assert ends["edge/managed_edge"]["condition"] == "satisfied"  # found, reused, untouched
        assert (
            "edge/docker_edge" not in ends
        )  # the alternative the selection left out is not walked
        assert not_passed == {"gateway": "blocked"}  # row 9
    else:
        carrier = ends["client"]
        assert (carrier["condition"], carrier["code"]) == ("blocked", "execution.toolchain_missing")
        assert live_state.TOOL in carrier["human_action"]  # the identifier the code names
        (issue,) = _issues(rig, "client")
        confirmations = [r for r in rig.rows() if r["class"] == "confirmation"]
        assert [c["status"] for c in confirmations if c["path"] == "client"] == ["not_applied"]
        assert issue["effect"] == live_state.CREATE_EFFECT
        assert carrier["cut"] is None and carrier["provenance"] == "absent"
        assert not_passed == {"client": "blocked"}  # row 9
    # one condition, one class: no other vertex is stopped or failed
    assert len(not_passed) == 1


@proves
def test_condition_first_seen_mid_node(tmp_path: Path, markers: FakeMarker) -> None:
    """The instance is up when the selection observes it and gone by the time its node first
    observes it: the condition is first seen inside the node, after the plan identity. The node
    still stops before any effect (an externally managed one has none), with the same code, and
    the dependent is never started."""
    marker = markers
    port_map = live_state.prepare(marker, "healthy")  # `managed_data` is up
    reads = port_map[ports.ResourceReads]
    seen: list[str] = []

    class Vanishing:
        """`ResourceReads` over the marker whose instance of `managed_data` goes away after the
        selection has looked at it."""

        def __getattr__(self, name: str) -> Any:
            return getattr(reads, name)

        def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
            result = reads.observe(spec, lineage, effect)  # type: ignore[attr-defined]
            if spec.logical_system == "managed_data" and not seen:
                seen.append("selection")
                marker._remove("found-managed-data")  # noqa: SLF001 (the machine changes)
            return result

    port_map[ports.ResourceReads] = Vanishing()
    rig = _run(tmp_path, marker, "healthy", port_map)

    assert seen == ["selection"]
    plan = [r for r in rig.rows() if r["class"] == "plan"]
    assert plan[0]["selection"]["data"] == "data/managed_data"  # it was up when selected
    ends = rig.ends()
    carrier = ends["data/managed_data"]
    assert (carrier["condition"], carrier["code"]) == ("blocked", live_state.ABSENT_CODE)
    assert not _issues(rig, "data/managed_data") and not _issues(rig, "client")
    assert (ends["client"]["condition"], ends["client"]["cut"]) == (None, "not_started")
    assert _not_passed(rig) == {"data/managed_data": "blocked"}  # exactly one class
