"""L.TR-1.6: domain identifier sets and argument-filtered children.

A request value at an argument that a composite binds to a declared identifier set must be a
member of it: an unknown one is refused `admission.unknown_identifier` naming the value and where
the valid ones are listed, before any run id. When the binding filters children, the plan compiles
only the named children plus the closure of what they `needs`. Schema failures stay
`admission.invalid_args` (`validate_args` is unchanged and runs first)."""

from __future__ import annotations

import pytest

from tests.tree.test_tr1_admission import publish, refused, source
from trestle.common import codes
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import AdmitRequest
from trestle.server.admission import plan_for_admission
from trestle.server.main import Kernel

DEADLINE_S = 300.0


def plan_of(kernel: Kernel, plugin: str, args: dict[str, object]) -> object:
    snap = kernel.registry.get(plugin)
    assert snap is not None
    return plan_for_admission(snap, AdmitRequest(plugin=plugin, args=args), DEADLINE_S)


@pytest.mark.proves("WR-PLAN-2", "WR-PLAN-2:domain-identifier", "A", "tree", "MCP+LOGIC", "CI")
def test_unknown_identifier_refused_names_it_and_set(tree_kernel: Kernel) -> None:
    name = publish(tree_kernel, source("identifier_sets"))
    outcome = refused(tree_kernel, name, {"only": "nonesuch"})
    assert outcome.code == codes.UNKNOWN_IDENTIFIER == "admission.unknown_identifier"
    assert "nonesuch" in outcome.message, outcome.message  # the value
    assert "identifier_sets.services" in outcome.message, outcome.message  # where the set is listed
    # a listed value is no unknown identifier: it compiles and reaches the temporary code
    listed = refused(tree_kernel, name, {"only": "web"})
    assert listed.code == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED
    # a schema failure keeps its own code: the schema check runs before the identifier check
    schema = refused(tree_kernel, name, {"only": 7})
    assert schema.code == codes.INVALID_ARGS


@pytest.mark.proves("WR-PLAN-2", "WR-PLAN-2:domain-identifier", "A", "tree", "MCP+LOGIC", "CI")
def test_filter_selects_needs_closure(tree_kernel: Kernel) -> None:
    """`web` needs `api`: naming `web` compiles exactly `web` and `api`, naming `worker` compiles
    `worker` alone, and naming nothing compiles every child."""
    name = publish(tree_kernel, source("identifier_sets"))
    expected = {
        "web": {"", "web", "api"},
        "worker": {"", "worker"},
        "api": {"", "api"},
    }
    for value, paths in expected.items():
        plan = plan_of(tree_kernel, name, {"only": value})
        assert isinstance(plan, AdmittedPlan), (value, plan)
        assert set(plan.paths) == paths, value
        assert set(plan.vertex("").children) == paths - {""}, value
    everything = plan_of(tree_kernel, name, {})
    assert isinstance(everything, AdmittedPlan)
    assert set(everything.paths) == {"", "api", "web", "worker"}
