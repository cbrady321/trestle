"""C-3 (DEFERRED-DECISIONS Q4, as decided 2026-10-03): `services` selects the service children the
reference tree walks, with their dependency closure, at admission.

The catalog is the single user's own data: a service's `depends_on` (the Compose `depends_on`) is
the tree child's `needs`, and `compiler._select_refs` keeps a selected child's needs closure, so
selecting `postgres` admits `http_support` too (WR-ENV-1: the started set equals the closure) and a
service outside the closure is not a plan vertex. At run time `derive_closure` checks the user's
Compose definition agrees with the catalog, before any effect."""

from __future__ import annotations

import pytest
from trestle.common.plan import compiler
from trestle.workflow import ports
from trestle.workflow.extract import extract_declared_tree
from trestle.workflow.ports import Closure

from trestle_env import tree
from trestle_env.closure import COMPOSE_DEFINITION_INVALID
from trestle_env.plugins._bind import ClosureRefusedError, derive_closure

# the reference compose definition's closure (tests/fixtures/ref-compose/compose.yaml)
REFERENCE = Closure(
    frozenset({"http_support", "postgres"}), frozenset({("postgres", "http_support")}), "fp-ref"
)


class ReferenceResolver:
    """A `ComposeResolver` over the reference definition: `postgres` `depends_on` `http_support`."""

    def __init__(self, edges: frozenset[tuple[str, str]] = REFERENCE.edges) -> None:
        self._edges = edges

    def closure(self, project: str, selected: frozenset[str]) -> Closure:
        chosen, work = set(selected), list(selected)
        while work:
            name = work.pop()
            for owner, dep in self._edges:
                if owner == name and dep not in chosen:
                    chosen.add(dep)
                    work.append(dep)
        return Closure(frozenset(chosen), self._edges, "fp-ref")


def _vertices(request: dict[str, object]) -> set[str]:
    plan = compiler.compile(extract_declared_tree(tree.ENTRY), {"env": "e", **request})
    assert isinstance(plan, compiler.AdmittedPlan), plan
    return {v.path for v in plan.vertices}


def test_a_selection_admits_its_dependency_closure() -> None:
    assert _vertices({"services": ["postgres"]}) >= {
        tree.HTTP_SUPPORT_SERVICE,
        tree.POSTGRES_SERVICE,
    }


def test_a_closed_selection_admits_only_itself() -> None:
    walked = _vertices({"services": ["http_support"]})
    assert tree.HTTP_SUPPORT_SERVICE in walked
    assert tree.POSTGRES_SERVICE not in walked


def test_no_selection_walks_every_service() -> None:
    walked = _vertices({})
    assert {tree.HTTP_SUPPORT_SERVICE, tree.POSTGRES_SERVICE} <= walked


def test_a_compose_definition_that_agrees_with_the_catalog_is_derived() -> None:
    for selected in (["http_support"], ["postgres"]):
        plan = derive_closure({ports.ComposeResolver: ReferenceResolver()}, selected)
        assert plan is not None
        assert plan.services == tree.CATALOG.dependency_closure(selected)


def test_a_compose_definition_that_disagrees_is_refused_naming_both_sets() -> None:
    no_edges = ReferenceResolver(frozenset())  # the definition drops postgres -> http_support
    with pytest.raises(ClosureRefusedError) as raised:
        derive_closure({ports.ComposeResolver: no_edges}, ["postgres"])
    assert raised.value.code == COMPOSE_DEFINITION_INVALID
    assert "walks http_support, postgres" in raised.value.identifier
    assert "closure is postgres;" in raised.value.identifier
