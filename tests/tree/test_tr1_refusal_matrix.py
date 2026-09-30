"""L.TR-1.7: the refusal matrix, every ground through both host entry points.

Each ground a tree can be refused for returns its own code and names its identifier through
`ControlSurface` and through the MCP tools, with no run dir, ledger or process. A ground the
declaration alone shows is refused at publication (`publication.*`, L.TR-0.4 and L.TR-1.5): a
published declaration cannot reach admission's defensive second reach for it, so the same ground
is also driven at admission by planting the declaration below publication (`planting.py`). A
valid `AllDeclaration` or `ChoiceNode` tree still gets `admission.plan_multi_vertex_unsupported`,
and the register shows A2.5 and A5.3 not served (DM-12)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, register
from tests.tree import hostpath, planting
from tests.tree.planting import Nodes
from tests.tree.test_tr1_admission import descendants, edited, source
from tests.tree.test_tr1_identity import by_argument
from trestle.common import codes
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.main import Kernel

STAGE_PUBLISH = "publish"
STAGE_RUN = "run"


@dataclass(frozen=True)
class Ground:
    """One way a tree is refused. `text` is the plugin source; `repaired` (planted grounds) the
    publishable source whose snapshot `plant` then rewrites; `args` the request; `code` and
    `naming` what the refusal is and the identifiers its message names."""

    text: Callable[[], str]
    stage: str
    code: str
    naming: tuple[str, ...]
    args: dict[str, Any] | None = None
    plant: Callable[[Nodes], None] | None = None


def renamed(text: str, old: str, new: str) -> str:
    """`text` under another plugin name, so both host paths can hold every ground at once."""
    assert text.count(f"def {old}(") == 1
    return text.replace(f"def {old}(", f"def {new}(")


CYCLE_FIXED = (
    'ChildBinding(unit="a", params={}, needs=("b",))',
    'ChildBinding(unit="a", params={}, needs=())',
)
GHOST_FIXED = (
    'units={"haunted": group("haunted", (ChildBinding(unit="ghost", params={}, needs=()),))},',
    'units={"haunted": group("haunted", (ChildBinding(unit="ghost", params={}, needs=()),)),'
    ' "ghost": leaf("ghost")},',
)


def _cycle(nodes: Nodes) -> None:
    planting.child(nodes, "", "a")["binding"].update(needs=["b"])


def _ghost(nodes: Nodes) -> None:
    planting.child(nodes, "", "ghost").update(path=None)


GROUNDS: dict[str, Ground] = {
    # refused at publication
    "cycle": Ground(
        lambda: source("cycle"), STAGE_PUBLISH, codes.PUBLICATION_DEPENDENCY_CYCLE, ("a",)
    ),
    "conflict": Ground(
        lambda: source("conflict"),
        STAGE_PUBLISH,
        codes.PUBLICATION_DECLARATION_CONFLICT,
        ("worker",),
    ),
    "unknown_descendant": Ground(
        lambda: source("unknown_descendant"),
        STAGE_PUBLISH,
        codes.PUBLICATION_UNIT_UNRESOLVED,
        ("ghost",),
    ),
    "uncovered_precondition": Ground(
        lambda: source("uncovered_precondition"),
        STAGE_PUBLISH,
        codes.PUBLICATION_PLAN_PRECONDITION_UNCOVERED,
        ("consumer", "producer_ready"),
    ),
    # refused at admission
    "misfit": Ground(lambda: source("misfit"), STAGE_RUN, codes.BUDGET_DOES_NOT_FIT, ("slow",)),
    "lease_mismatch": Ground(
        lambda: source("lease_pair"),
        STAGE_RUN,
        codes.LEASE_SET_UNDECIDABLE,
        ("second",),
        {"env": "dev", "env_b": "staging"},
    ),
    "unknown_identifier": Ground(
        lambda: source("identifier_sets"),
        STAGE_RUN,
        codes.UNKNOWN_IDENTIFIER,
        ("nonesuch", "identifier_sets.services"),
        {"only": "nonesuch"},
    ),
    "conflict_by_argument": Ground(
        by_argument,
        STAGE_RUN,
        codes.DECLARATION_CONFLICT,
        ("worker",),
        {"size_a": 1, "size_b": 2},
    ),
    # the same publication grounds reached at admission (the defensive second reach)
    "cycle_at_admission": Ground(
        lambda: renamed(edited("cycle", *CYCLE_FIXED), "cycle", "cycle_at_admission"),
        STAGE_RUN,
        codes.DEPENDENCY_CYCLE,
        ("a",),
        None,
        _cycle,
    ),
    "unit_unresolved_at_admission": Ground(
        lambda: renamed(
            edited("unknown_descendant", *GHOST_FIXED),
            "unknown_descendant",
            "unresolved_at_admission",
        ),
        STAGE_RUN,
        codes.UNIT_UNRESOLVED,
        ("ghost",),
        None,
        _ghost,
    ),
}
PATHS = ("control", "mcp")


@pytest.fixture(scope="module")
def mcp(tmp_path_factory: pytest.TempPathFactory) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path_factory.mktemp("matrix-host")) as host:
        yield host


def _no_runs(home: Path) -> None:
    runs = home / "runs"
    assert not runs.exists() or not [p for p in runs.glob("*/*") if p.is_dir()], "a run dir exists"
    assert not list(runs.glob("**/ledger*")) if runs.exists() else True


def _check(outcome: dict[str, Any], ground: Ground) -> None:
    assert outcome.get("code") == ground.code, outcome
    assert "run_id" not in outcome, outcome
    for identifier in ground.naming:
        assert identifier in outcome["message"], (identifier, outcome["message"])


def _refuse_by_control(kernel: Kernel, ground: Ground) -> dict[str, Any]:
    text = ground.text()
    result: PublishView | RunView | RequestOutcome
    if ground.stage == STAGE_PUBLISH:
        result = hostpath.publish_tree_via_host(kernel, text)
    else:
        published = hostpath.publish_tree_via_host(kernel, text)
        assert isinstance(published, PublishView), published
        snap = kernel.registry.get(published.name)
        assert snap is not None
        if ground.plant is not None:
            planting.plant(snap, ground.plant)
        result = hostpath.run_tree_via_host(kernel, published.name, ground.args)
    assert isinstance(result, RequestOutcome), result
    return result.to_dict()


def _refuse_by_mcp(host: mcp_host.McpHost, ground: Ground) -> dict[str, Any]:
    text = ground.text()
    if ground.stage == STAGE_PUBLISH:
        return hostpath.mcp_publish_tree(host, text)
    published = hostpath.mcp_publish_tree(host, text)
    assert "code" not in published, published
    if ground.plant is not None:
        planting.plant_snapshot_dir(
            host.home / "snapshots" / str(published["snapshot_id"]), ground.plant
        )
    return hostpath.mcp_run_tree(host, str(published["name"]), ground.args)


@pytest.mark.proves("A2.5", "A2.5", "A", "tree", "MCP", "CI")
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("ground", sorted(GROUNDS))
def test_refused(ground: str, path: str, tree_kernel: Kernel, mcp: mcp_host.McpHost) -> None:
    spec = GROUNDS[ground]
    home = tree_kernel.home if path == "control" else mcp.home
    before = descendants()
    if path == "control":
        outcome = _refuse_by_control(tree_kernel, spec)
    else:
        outcome = _refuse_by_mcp(mcp, spec)
    _check(outcome, spec)
    _no_runs(home)  # no run dir, no ledger
    assert descendants() == before  # no process (MC-13): the server, if any, was already there


VALID_TREES = {"all": "shared_diamond", "choice": "choice_fake"}


@pytest.mark.proves("A5.3", "A5.3", "A", "tree", "MCP", "CI")
@pytest.mark.parametrize("tree", sorted(VALID_TREES))
def test_valid_tree_still_temp_refused(
    tree: str, tree_kernel: Kernel, mcp: mcp_host.McpHost
) -> None:
    """A valid tree compiles and is refused only by the temporary rule, through both entry points
    (L.TR-L.1 lifts it for `all` and L.TR-5.3 for `choice`)."""
    text = source(VALID_TREES[tree])
    published = hostpath.publish_tree_via_host(tree_kernel, text)
    assert isinstance(published, PublishView), published
    refused = hostpath.run_tree_via_host(tree_kernel, published.name)
    assert isinstance(refused, RequestOutcome), refused
    assert refused.code == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED
    _no_runs(tree_kernel.home)
    on_wire = hostpath.mcp_publish_tree(mcp, text)
    assert "code" not in on_wire, on_wire
    answer = hostpath.mcp_run_tree(mcp, str(on_wire["name"]))
    assert answer.get("code") == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED, answer
    _no_runs(mcp.home)


def test_a25_and_a53_not_served() -> None:
    """The register holds neither A2.5 nor A5.3 (DM-12): a claim of a label composing either is
    not blocked by any present entry. (`meta register` as a whole stays non-zero until the lift
    set clears, L.TR-L.1: it reports the labels composing A1.5, A2.6, A4.2, A5.4, A6.3, A6.4, A8.4
    and A8.5.)"""
    from tests.proof import meta

    labels = [lb for lb in meta._load_all_labels() if lb.get("composes") in ("A2.5", "A5.3")]
    assert labels, "no label composes A2.5 or A5.3"
    passing: dict[str, list[dict[str, Any]]] = {
        str(lb["id"]): [{"gap": None, "strict_xfail": False}] for lb in labels
    }
    held = register.register_violations(register.load_entries(), passing)
    assert not {lb["id"] for lb in labels} & set(held), held
