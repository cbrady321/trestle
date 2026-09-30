"""The Compose closure is derived in the composition root before any effect (L.RB-1.1, AMB-4)."""

from __future__ import annotations

import pytest
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind
from trestle.workflow.ports import Closure, ClosureRefused

from trestle_env import tree
from trestle_env.closure import UNKNOWN_IDENTIFIER
from trestle_env.plugins._bind import ClosureRefusedError, derive_closure


class FixedResolver:
    """A `ComposeResolver` that answers one closure (or refusal) and records what it was asked."""

    def __init__(self, answer: Closure | ClosureRefused) -> None:
        self.answer = answer
        self.asked: list[tuple[str, frozenset[str]]] = []

    def closure(self, project: str, selected: frozenset[str]) -> Closure | ClosureRefused:
        self.asked.append((project, selected))
        return self.answer


def test_the_closure_of_the_selection_is_derived_before_any_effect() -> None:
    resolver = FixedResolver(Closure(frozenset({"postgres"}), frozenset(), "fp-1"))
    plan = derive_closure({ports.ComposeResolver: resolver}, ["postgres"])
    assert plan is not None
    assert [(n.service, n.realization) for n in plan.nodes] == [
        ("postgres", RealizationKind.DOCKER_SERVICE)
    ]
    assert plan.definition_fingerprint == "fp-1"
    assert resolver.asked == [("reference", frozenset({"postgres"}))]


def test_no_selection_means_every_catalog_service() -> None:
    everything = frozenset(str(s.id) for s in tree.CATALOG.services)
    resolver = FixedResolver(Closure(everything, frozenset(), "fp-1"))
    derive_closure({ports.ComposeResolver: resolver})
    assert resolver.asked == [("reference", everything)]


def test_a_closure_naming_a_service_the_catalog_lacks_is_refused_with_its_code() -> None:
    resolver = FixedResolver(Closure(frozenset({"postgres", "mystery"}), frozenset(), "fp-2"))
    with pytest.raises(ClosureRefusedError) as raised:
        derive_closure({ports.ComposeResolver: resolver}, ["postgres"])
    assert raised.value.code == UNKNOWN_IDENTIFIER
    assert raised.value.identifier == "mystery"


def test_a_resolver_refusal_passes_its_code_through_unchanged() -> None:
    refusal = ClosureRefused("adapter.compose_definition_invalid", "no services")
    with pytest.raises(ClosureRefusedError) as raised:
        derive_closure({ports.ComposeResolver: FixedResolver(refusal)}, ["postgres"])
    assert (raised.value.code, raised.value.identifier) == (refusal.code, refusal.subject)


def test_without_a_bound_definition_nothing_is_derived() -> None:
    assert derive_closure({}, ["postgres"]) is None
