"""L.TR-1.1: admission compiles the whole declared tree before the temporary refusal.

`Admission.admit` loads the snapshot's declared tree (MC-34) and runs the plan compiler (MC-23)
in B2-C2's check order, so a tree defective in a way the declaration alone shows is refused with
its own code naming the identifier, before any run id; a valid tree is admitted (`AllDeclaration`
since L.TR-L.1, `ChoiceNode` since L.TR-5.3, which removed the temporary refusal). The defective
trees are planted below publication (`tests/tree/planting.py`; L.TR-0.4 refuses them at
publication)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.proof import ancestry
from tests.tree import planting
from trestle.common import codes
from trestle.common.types import AdmitRequest, AdmitResultAdmitted, PublishView, RequestOutcome
from trestle.server import idempotency
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[2]
TREES = REPO / "tests" / "fixtures" / "trees"
KEY = "tr1-admission-key"


def source(name: str) -> str:
    return (TREES / f"{name}.py").read_text(encoding="utf-8")


def edited(name: str, old: str, new: str) -> str:
    text = source(name)
    assert text.count(old) == 1, f"{name}: {old!r} moved"
    return text.replace(old, new)


def publish(kernel: Kernel, text: str) -> str:
    view = kernel.control.publish_plugin(text)
    assert isinstance(view, PublishView), view
    return view.name


def run_dirs(kernel: Kernel) -> list[Path]:
    runs = kernel.home / "runs"
    return sorted(p for p in runs.glob("*/*") if p.is_dir()) if runs.exists() else []


def descendants() -> set[int]:
    """The pids of every live descendant of this process, but for the `ps` the snapshot runs."""
    procs = [p for p in ancestry.snapshot() if os.path.basename(p.argv.split(" ", 1)[0]) != "ps"]
    found: set[int] = set()
    frontier = {os.getpid()}
    while frontier:
        frontier = {p.pid for p in procs if p.ppid in frontier} - found
        found |= frontier
    return found


def refused(kernel: Kernel, plugin: str, args: dict[str, object] | None = None) -> RequestOutcome:
    """The outcome of `run`, which must be a refusal that minted nothing: no run dir, no
    idempotency record, no process (MC-13)."""
    before = descendants()
    outcome = kernel.control.run(plugin=plugin, args=args or {}, idempotency_key=KEY)
    assert isinstance(outcome, RequestOutcome), outcome
    assert outcome.origin == "admission" and outcome.retryable is False
    assert "run_id" not in outcome.to_dict()
    assert run_dirs(kernel) == []
    assert idempotency.lookup(kernel.home, KEY) is None
    assert descendants() == before
    return outcome


def admitted(
    kernel: Kernel, plugin: str, args: dict[str, object] | None = None
) -> AdmitResultAdmitted:
    """A valid tree through admission alone (`AllDeclaration` since L.TR-L.1, `ChoiceNode` since
    L.TR-5.3): a run id is minted
    and its run directory written, and admission itself spawns no process (MC-13)."""
    before = descendants()
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args=args or {}))
    assert isinstance(result, AdmitResultAdmitted), result
    assert result.run_id.startswith("r_")
    assert [d.name for d in run_dirs(kernel)][-1:] == [result.run_id]
    assert descendants() == before
    return result


CYCLE_FIXED = (
    'ChildBinding(unit="a", params={}, needs=("b",))',
    'ChildBinding(unit="a", params={}, needs=())',
)
GHOST_FIXED = (
    'units={"haunted": group("haunted", (ChildBinding(unit="ghost", params={}, needs=()),))},',
    'units={"haunted": group("haunted", (ChildBinding(unit="ghost", params={}, needs=()),)),'
    ' "ghost": leaf("ghost")},',
)


def planted_cycle(kernel: Kernel) -> str:
    name = publish(kernel, edited("cycle", *CYCLE_FIXED))
    snap = kernel.registry.get(name)
    assert snap is not None
    planting.plant(
        snap, lambda nodes: planting.child(nodes, "", "a")["binding"].update(needs=["b"])
    )
    return name


def planted_unknown_unit(kernel: Kernel) -> str:
    name = publish(kernel, edited("unknown_descendant", *GHOST_FIXED))
    snap = kernel.registry.get(name)
    assert snap is not None
    planting.plant(snap, lambda nodes: planting.child(nodes, "", "ghost").update(path=None))
    return name


def snapshots(kernel: Kernel) -> set[str]:
    return {p.name for p in (kernel.home / "snapshots").glob("snap_*")}


@pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:unknown-or-cycle-no-run-id", "A", "tree", "MCP+LOGIC", "CI"
)
def test_unknown_unit_refused_before_run_id(tree_kernel: Kernel) -> None:
    name = planted_unknown_unit(tree_kernel)
    seen = snapshots(tree_kernel)
    outcome = refused(tree_kernel, name)
    assert outcome.code == codes.UNIT_UNRESOLVED == "admission.unit_unresolved"
    assert "ghost" in outcome.message, outcome.message  # the identifier, in the message
    assert snapshots(tree_kernel) == seen  # MC-13: the snapshot store is unchanged


@pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:refusal-names-identifier", "A", "tree", "MCP+LOGIC", "CI"
)
@pytest.mark.parametrize(
    ("planter", "code", "identifier"),
    [
        (planted_cycle, codes.DEPENDENCY_CYCLE, "(a)"),
        (planted_unknown_unit, codes.UNIT_UNRESOLVED, "ghost"),
    ],
    ids=["cycle", "unknown_unit"],
)
def test_invalid_tree_gets_specific_code_not_temp(
    tree_kernel: Kernel, planter: object, code: str, identifier: str
) -> None:
    name = planter(tree_kernel)  # type: ignore[operator]
    outcome = refused(tree_kernel, name)
    assert outcome.code == code and outcome.code != codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED
    assert identifier in outcome.message, outcome.message


def test_valid_all_tree_admitted(tree_kernel: Kernel) -> None:
    """A valid `AllDeclaration` tree compiles and is admitted (L.TR-L.1 lifted the temporary
    refusal for it, MC-B3-03 order): a run id is minted."""
    name = publish(tree_kernel, source("shared_diamond"))
    admitted(tree_kernel, name)


def test_valid_choice_tree_admitted(tree_kernel: Kernel) -> None:
    """A valid `ChoiceNode` tree compiles and is admitted (L.TR-5.3 removed the last temporary
    refusal, MC-B3-03 order): a run id is minted."""
    name = publish(tree_kernel, source("choice_fake"))
    admitted(tree_kernel, name)
