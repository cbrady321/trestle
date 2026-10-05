"""Closure derivation over the Compose resolver's `Closure`, with overrides (L.NW-3.2)."""

from __future__ import annotations

import random
from typing import Any

import pytest
from trestle.common import codes as library_codes
from trestle.workflow.declarations import RealizationKind
from trestle.workflow.ports import Closure, ClosureRefused

from trestle_env import closure as closure_module
from trestle_env.catalog import Catalog, ServiceId
from trestle_env.closure import (
    BOUND_EXCEEDED,
    INVALID_ARGS,
    SELECT_MAX,
    UNKNOWN_IDENTIFIER,
    ClosurePlan,
    Refused,
    closure,
)


def catalog_of(
    graph: dict[str, list[str]], overrides: list[tuple[str, str]] | None = None
) -> Catalog:
    data: dict[str, Any] = {
        "schema": 1,
        "env_key": "env",
        "services": [{"id": name, "selector": name} for name in graph],
        "projects": [
            {"id": "local-py", "pin": {"python": "3.12"}, "tasks": [{"id": "serve", "argv": ["a"]}]}
        ],
        "overrides": [
            {"id": oid, "service": svc, "project": "local-py", "task": "serve"}
            for oid, svc in (overrides or [])
        ],
    }
    return Catalog.from_data(data)


def compose_closure(graph: dict[str, list[str]], selected: set[str]) -> Closure:
    """What a resolver returns: the selection plus its transitive dependencies, its edges."""
    members: set[str] = set()
    pending = sorted(selected)
    while pending:
        name = pending.pop()
        if name not in members:
            members.add(name)
            pending.extend(graph[name])
    edges = frozenset((n, d) for n in members for d in graph[n])
    return Closure(frozenset(members), edges, "fp-" + "-".join(sorted(graph)))


def random_graph(rng: random.Random) -> dict[str, list[str]]:
    size = rng.randint(1, 12)
    names = [f"svc{i}" for i in range(size)]
    return {
        n: sorted(rng.sample(names[:i], rng.randint(0, min(i, 3)))) for i, n in enumerate(names)
    }


@pytest.mark.parametrize("seed", range(40))
def test_closure_properties_over_seeded_catalogs(seed: int) -> None:
    rng = random.Random(seed)
    graph = random_graph(rng)
    names = sorted(graph)
    selected = set(rng.sample(names, rng.randint(1, len(names))))
    victims = [n for n in names if rng.random() < 0.4]
    overrides = [(f"local-{n}", n) for n in victims]
    catalog = catalog_of(graph, overrides)
    compose = compose_closure(graph, selected)
    plan = closure(catalog, compose, selected, [o for o, _ in overrides])
    assert isinstance(plan, ClosurePlan)
    services = {str(s) for s in plan.services}
    assert selected <= services  # closure ⊇ selected
    assert services == set(compose.services)
    for node in plan.nodes:  # closed under depends_on, override or not
        assert {str(d) for d in node.needs} == set(graph[str(node.service)])
        assert {str(d) for d in node.needs} <= services
        assert node.readiness == node.service
        assert (node.override is not None) == (str(node.service) in victims), (
            "exactly the overridden services in the closure run locally"
        )
    # deterministic: order of the selection and of the overrides is nothing
    again = closure(
        catalog, compose, sorted(selected, reverse=True), [o for o, _ in reversed(overrides)]
    )
    assert again == plan
    assert [str(n.service) for n in plan.nodes] == sorted(services)


@pytest.mark.proves(
    "WR-ENV-2", "WR-ENV-2:override-keeps-deps-and-readiness", "B", "B", "LOGIC", "CI"
)
def test_override_substitutes_node_keeping_deps_and_readiness() -> None:
    graph = {"db": [], "api": ["db"], "web": ["api"]}
    catalog = catalog_of(graph, [("api-local", "api")])
    compose = compose_closure(graph, {"web"})
    docker = closure(catalog, compose, {"web"})
    local = closure(catalog, compose, {"web"}, ["api-local"])
    assert isinstance(docker, ClosurePlan) and isinstance(local, ClosurePlan)
    before, after = docker.node("api"), local.node("api")
    assert before is not None and after is not None
    assert before.realization is RealizationKind.DOCKER_SERVICE
    assert after.realization is RealizationKind.AGENT_LAUNCHED_PROJECT
    assert after.needs == before.needs == {ServiceId("db")}  # its dependencies survive
    assert after.readiness == before.readiness == "api"  # so does its readiness contract
    assert (after.override, after.project, after.task) == ("api-local", "local-py", "serve")
    web = local.node("web")
    assert web is not None and web.needs == {ServiceId("api")}  # dependents still wait on it
    assert local.services == docker.services  # the closure's membership is unchanged
    assert local.node("db") == docker.node("db")  # nothing else moved
    assert [
        n
        for n in local.nodes
        if n.realization is RealizationKind.DOCKER_SERVICE and n.service == "api"
    ] == []  # never a Docker node for the replaced service


def test_an_override_outside_the_closure_changes_nothing() -> None:
    graph = {"db": [], "api": ["db"], "cache": []}
    catalog = catalog_of(graph, [("cache-local", "cache")])
    compose = compose_closure(graph, {"api"})
    assert closure(catalog, compose, {"api"}, ["cache-local"]) == closure(catalog, compose, {"api"})


def test_a_refused_closure_passes_its_code_through_unchanged() -> None:
    catalog = catalog_of({"a": []})
    for code in (UNKNOWN_IDENTIFIER, "adapter.compose_definition_invalid", BOUND_EXCEEDED):
        refused = closure(catalog, ClosureRefused(code, "the subject"), {"a"})
        assert refused == Refused(code, "the subject")


def test_a_service_the_catalog_lacks_is_refused_and_named() -> None:
    catalog = catalog_of({"a": []})
    compose = Closure(frozenset({"a", "stranger"}), frozenset({("a", "stranger")}), "fp")
    assert closure(catalog, compose, {"a"}) == Refused(UNKNOWN_IDENTIFIER, "stranger")


def test_a_selected_name_outside_the_closure_and_an_unknown_override_are_refused() -> None:
    catalog = catalog_of({"a": [], "b": []})
    compose = compose_closure({"a": [], "b": []}, {"a"})
    assert closure(catalog, compose, {"a", "b"}) == Refused(UNKNOWN_IDENTIFIER, "b")
    assert closure(catalog, compose, {"a"}, ["no-such"]) == Refused(UNKNOWN_IDENTIFIER, "no-such")


def test_two_overrides_of_one_service_are_refused() -> None:
    graph = {"a": []}
    catalog = catalog_of(graph, [("one", "a"), ("two", "a")])
    compose = compose_closure(graph, {"a"})
    assert closure(catalog, compose, {"a"}, ["one", "two"]) == Refused(INVALID_ARGS, "a")


def test_a_selection_over_the_answer_bound_is_refused() -> None:
    graph = {f"s{i}": [] for i in range(SELECT_MAX + 1)}
    catalog = catalog_of(graph)
    refused = closure(catalog, compose_closure(graph, set(graph)), set(graph))
    assert isinstance(refused, Refused) and refused.code == BOUND_EXCEEDED
    ok = {f"s{i}" for i in range(SELECT_MAX)}
    assert isinstance(closure(catalog, compose_closure(graph, ok), ok), ClosurePlan)


def test_the_codes_are_the_boundarys() -> None:
    assert closure_module.INVALID_ARGS == library_codes.INVALID_ARGS
    assert closure_module.UNKNOWN_IDENTIFIER == "admission.unknown_identifier"
    assert closure_module.BOUND_EXCEEDED == "admission.bound_exceeded"


def test_the_plan_carries_no_order_and_derives_none() -> None:
    fields = set(ClosurePlan.__dataclass_fields__)
    assert fields == {"nodes", "definition_fingerprint"}
