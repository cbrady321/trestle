"""L.RB-8.2: two overrides make a plan that is the same whatever order they are named in, and the
same on every admission (D-11; WR-ENV-2; `trestle_env.realization.choice_for` over the catalog).

With fakes: declaration-only units, no port and no process. Two supporting services of a test
catalog (`fixtures/two-overrides/catalog.json`: the reference catalog plus `postgres_local`, which
the shipped one dropped, decision B) are each a CHOICE between their Docker node and the catalog's
agent-launched override; a request that names both overrides selects both local alternatives, the
plan lists them (never a Docker node), and its digest is a function of the request's SET of
overrides alone. A request that names one override pins the other CHOICE to its fallback (V-7.2 step
1, decision A). Real routing (an externally managed realization) is undecided (OQ-25): the reference
space declares none."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from trestle.common.plan import carving, compiler
from trestle.workflow import (
    AllDeclaration,
    ArgBinding,
    ChildBinding,
    ChoiceNode,
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.declarations import RealizationKind
from trestle.workflow.extract import extract_root

from trestle_env import realization, tree
from trestle_env.catalog import Catalog, load_reference
from trestle_env.schema import OVERRIDES_ARG

CATALOG = Catalog.load(Path(__file__).parents[1] / "fixtures" / "two-overrides" / "catalog.json")
SERVICES = ("http_support", "postgres")
OVERRIDES = {"http_support": "http_support_local", "postgres": "postgres_local"}
DOCKER = {name: f"{name}.docker" for name in SERVICES}
READY = "ready"
DEADLINE_S = 300.0
RESERVE_S = 10.0
RELEASE_SLICE_S = 10.0


class Unit:
    """A declaration-only work unit: this proof carries no behaviour."""

    def __init__(self, unit: str) -> None:
        self._unit = unit

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition=READY,
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=10)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=20),
            max_attempts=1,
        )


def entry() -> WorkflowEntry:
    units: dict[str, Any] = {}
    children = []
    for name in SERVICES:
        choice = f"backend.{name}"
        units[choice] = ChoiceNode(
            unit=choice,
            flags=LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE),
            choice=realization.choice_for(CATALOG, name, DOCKER[name], READY),
            budget=timedelta(seconds=40),
        )
        units[DOCKER[name]] = Unit(DOCKER[name])
        units[OVERRIDES[name]] = Unit(OVERRIDES[name])
        children.append(ChildBinding(unit=choice, params={}, needs=(), name=name))
    units["overrides_env"] = AllDeclaration(
        unit="overrides_env",
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=tuple(children),
        concurrency=2,
        budget=timedelta(seconds=120),
        identifier_sets=tree.identifier_sets(CATALOG),
        arg_bindings=(ArgBinding(OVERRIDES_ARG, tree.OVERRIDES_SET, False),),
        env_key_field="env",
    )
    return WorkflowEntry(root="overrides_env", units=units, deadline=timedelta(seconds=DEADLINE_S))


def admit(request: dict[str, Any]) -> compiler.AdmittedPlan:
    _, declared = extract_root(entry())
    compiled = compiler.compile(declared, request)
    assert isinstance(compiled, compiler.AdmittedPlan), compiled
    release_slice = carving.release_slice_for(compiled, RELEASE_SLICE_S)
    slices = carving.carve(compiled, DEADLINE_S, RESERVE_S, release_slice)
    assert isinstance(slices, dict), slices
    return carving.attach(compiled, slices, release_slice)


def units_in(plan: compiler.AdmittedPlan) -> set[str]:
    return {v.path.rsplit("/", 1)[-1] for v in plan.vertices}


@pytest.mark.proves("WR-ENV-2", "WR-ENV-2:real-routing", "B", "B", "LOGIC", "CI")
def test_real_routing_is_undeclared_while_oq_25_is_open() -> None:
    kinds = {
        member.kind for name in SERVICES for member in realization.realization_space(CATALOG, name)
    }
    assert kinds == {RealizationKind.DOCKER_SERVICE, RealizationKind.AGENT_LAUNCHED_PROJECT}
    assert all(
        member.human_action is None
        for name in SERVICES
        for member in realization.realization_space(CATALOG, name)
    )


def test_choice_for_is_the_realization_space_docker_first_then_each_override() -> None:
    choice = realization.choice_for(CATALOG, "http_support", "http_support.docker", READY)
    assert [a.unit for a in choice.alternatives] == ["http_support.docker", "http_support_local"]
    assert choice.select_arg == OVERRIDES_ARG and choice.fallback == "http_support.docker"
    assert choice.readiness == READY and choice.logical_system == "http_support"
    docker, local = choice.alternatives
    assert docker.reachable_from > local.reachable_from  # a container reaches only the Docker node
    with pytest.raises(ValueError):
        realization.choice_for(CATALOG, "no-such-service", "x", READY)


def test_two_overrides_same_plan_digest() -> None:
    both = ["http_support_local", "postgres_local"]
    forward = admit({"env": "e", OVERRIDES_ARG: both})
    backward = admit({"env": "e", OVERRIDES_ARG: list(reversed(both))})
    again = admit({"env": "e", OVERRIDES_ARG: both})
    assert forward.plan_digest and forward.plan_digest == backward.plan_digest == again.plan_digest
    assert forward.to_json() == backward.to_json() == again.to_json()
    # the plan holds both local alternatives and no Docker node: the override replaces it
    assert {"http_support_local", "postgres_local"} <= units_in(forward)
    assert not units_in(forward) & set(DOCKER.values())


def test_a_request_that_names_none_of_a_choices_alternatives_pins_its_fallback() -> None:
    """V-7.2 step 1 (decision A): `overrides` names only postgres's override, so http_support's
    CHOICE has its fallback alone eligible: postgres local, http_support in Docker."""
    only = admit({"env": "e", OVERRIDES_ARG: ["postgres_local"]})
    assert dict(only.eligible) == {
        "http_support": ("http_support/http_support.docker",),
        "postgres": ("postgres/postgres_local",),
    }
    assert {"postgres_local", "http_support.docker"} <= units_in(only)
    assert not units_in(only) & {"http_support_local", "postgres.docker"}
    again = admit({"env": "e", OVERRIDES_ARG: ["postgres_local"]})
    assert only.plan_digest == again.plan_digest  # D-11: the same request, the same plan
    other_env = admit({"env": "other", OVERRIDES_ARG: ["http_support_local", "postgres_local"]})
    both = admit({"env": "e", OVERRIDES_ARG: ["http_support_local", "postgres_local"]})
    assert other_env.plan_digest != both.plan_digest  # the environment key is part of the plan


def test_two_overrides_of_one_service_are_not_promoted() -> None:
    from trestle.workflow.ports import Closure

    from trestle_env.closure import closure

    catalog = load_reference()
    twin = catalog.override("http_support_local")
    assert twin is not None
    # OQ-25: a second override of one node is refused by the closure, naming the service
    compose = Closure(frozenset({"http_support"}), frozenset(), "f")
    duplicate = type(catalog)(
        catalog.services,
        catalog.projects,
        catalog.tests,
        (
            *catalog.overrides,
            type(twin)(type(twin.id)("http_support_local2"), twin.service, twin.project, twin.task),
        ),
        catalog.env_key,
    )
    refused = closure(
        duplicate, compose, {"http_support"}, ["http_support_local", "http_support_local2"]
    )
    assert getattr(refused, "code", None) == "admission.invalid_args"
