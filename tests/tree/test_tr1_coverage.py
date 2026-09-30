"""L.TR-1.5: a child precondition no upstream node covers is refused at publication.

V-8 L-7 / `B1-E1`: for a child, a precondition must be covered by a sibling `needs` whose
postcondition is that check (or by an inline check the node itself observes, which is code the
declaration cannot show and so the loop's own in-node check, unchanged, DM-34). Publication refuses
the rest with `publication.plan_precondition_uncovered` naming the node and the check, promoting no
snapshot. No case here runs the tree (A2c6-1): the child running after the upstream
postcondition-pass record is `L.TR-3.2`'s in-library and `L.TR-L.2`'s on the host."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import harness
from trestle.common.plan import compiler
from trestle.common.types import PublishView, RequestOutcome
from trestle.workflow.extract import extract_declared_tree

REPO = Path(__file__).resolve().parents[2]
TREES = REPO / "tests" / "fixtures" / "trees"
UNCOVERED = "publication.plan_precondition_uncovered"

proves_cross_node = pytest.mark.proves(
    "WR-PLAN-12", "WR-PLAN-12:cross-node-refused", "A", "tree", "LOGIC", "CI"
)

NO_NEEDS = 'ChildBinding(unit="consumer", params={}, needs=())'
NEEDS_PRODUCER = 'ChildBinding(unit="consumer", params={}, needs=("producer",))'


def _source(name: str) -> str:
    return (TREES / f"{name}.py").read_text(encoding="utf-8")


def _edited(name: str, old: str, new: str) -> str:
    source = _source(name)
    assert source.count(old) == 1, f"{name}: {old!r} moved"
    return source.replace(old, new)


def _snapshots(home: Path) -> set[str]:
    return {p.name for p in (home / "snapshots").glob("snap_*")}


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugins = tmp_path / "plugins"
    plugins.mkdir(parents=True)
    return harness.fresh_kernel([plugins], home=tmp_path / "home")


def _refused(result: object, node: str, check: str) -> None:
    assert isinstance(result, RequestOutcome), result
    assert result.code == UNCOVERED, result
    assert result.origin == "publication" and result.retryable is False
    assert node in result.message and check in result.message, result.message


@proves_cross_node
def test_uncovered_cross_node_precondition_refused_at_publication(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    covered = _edited("uncovered_precondition", NO_NEEDS, NEEDS_PRODUCER)
    published = kernel.control.publish_plugin(covered)
    assert isinstance(published, PublishView), published
    before = _snapshots(kernel.home)

    refused = kernel.control.publish_plugin(_source("uncovered_precondition"))
    _refused(refused, "consumer", "producer_ready")  # the node and the check, both named
    assert _snapshots(kernel.home) == before  # no snapshot promoted
    served = kernel.registry.get("uncovered_precondition")
    assert served is not None and served.snapshot_id == published.snapshot_id  # previous kept

    # a first publication of the uncovered tree leaves nothing behind either
    other = _kernel(tmp_path / "fresh")
    _refused(
        other.control.publish_plugin(_source("uncovered_precondition")),
        "consumer",
        "producer_ready",
    )
    assert not _snapshots(other.home)


@proves_cross_node
def test_upstream_covered_precondition_compiles(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    covered = _edited("uncovered_precondition", NO_NEEDS, NEEDS_PRODUCER)
    assert isinstance(kernel.control.publish_plugin(covered), PublishView)
    module_path = tmp_path / "covered.py"
    module_path.write_text(covered, encoding="utf-8")

    from tests.tree.test_fixtures import load_fixture

    module = load_fixture(module_path)
    plan = compiler.compile(extract_declared_tree(module.ENTRY), {})
    assert isinstance(plan, compiler.AdmittedPlan), plan  # MC-23 compiles it
    assert ("producer", "consumer") in plan.edges  # the precondition is covered through needs


@proves_cross_node
def test_coverage_follows_needs_transitively_and_down_from_the_parent(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    # a chain producer -> middle -> consumer covers the check through the middle sibling
    chain = _edited(
        "uncovered_precondition",
        NO_NEEDS,
        NEEDS_PRODUCER.replace('("producer",)', '("middle",)'),
    )
    chain = chain.replace(
        '                ChildBinding(unit="producer", params={}, needs=()),\n',
        '                ChildBinding(unit="producer", params={}, needs=()),\n'
        '                ChildBinding(unit="middle", params={}, needs=("producer",)),\n',
    ).replace(
        '        "consumer": leaf("consumer", pre=("producer_ready",)),',
        '        "middle": leaf("middle", post="middle_ready"),\n'
        '        "consumer": leaf("consumer", pre=("producer_ready",)),',
    )
    assert isinstance(kernel.control.publish_plugin(chain), PublishView)
    # dropping the link that reaches the producer leaves the check uncovered
    broken = chain.replace(
        'ChildBinding(unit="middle", params={}, needs=("producer",))',
        'ChildBinding(unit="middle", params={}, needs=())',
    )
    _refused(kernel.control.publish_plugin(broken), "consumer", "producer_ready")


@proves_cross_node
def test_check_established_only_by_a_non_upstream_node_stays_refused(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    # the consumer needs a node, but not the one whose postcondition is the check
    source = _edited("uncovered_precondition", NO_NEEDS, NEEDS_PRODUCER)
    source = source.replace('post="producer_ready"', 'post="another_ready"')
    _refused(kernel.control.publish_plugin(source), "consumer", "producer_ready")


@proves_cross_node
def test_one_vertex_coverage_behaviour_unchanged(tmp_path: Path) -> None:
    # the root leaf is judged by OQ-31's variant, not by this check: under the shipped variant
    # `sibling_only_leaf` still publishes (its in-node refusal stops it later), as at A-1
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(_source("sibling_only_leaf")), PublishView)
    # and every valid structural fixture still publishes
    for name in ("two_branch_barrier", "three_level", "slice_a_tree", "shared_diamond"):
        assert isinstance(kernel.control.publish_plugin(_source(name)), PublishView), name
