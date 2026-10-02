"""The Compose resolver's conformance cases (L.NW-2.4; B3-C13, B3-C17, B3-C20, MC-B-01).

Registered through the suite core's `register_family` as family `compose` and run, UNMODIFIED,
against the stdlib fake and the real `docker compose config --format json` resolver. A case
reaches the implementation only through `closure(project, selected)` and the factory's extras:

- `project`: a definition of five services, `web -> api -> db`, `worker -> db`, and `cache` alone;
- `changed_project`: the same services with one more dependency (`web -> cache`);
- `invalid_project`: a definition that cannot be read as one; `oversized_project`: one whose
  closure exceeds `IDSET_MAX`;
- `reach.envelope`: the directory the definitions live in, hashed around every call (a read
  writes nothing; `ComposeResolver.closure` has an empty `INCIDENTAL_WRITES`).

The closure is the selection plus its transitive dependencies, an edge is `(dependent,
dependency)`, and `definition_fingerprint` covers the whole definition's dependency structure.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tests.proof.suites.ports import core
from tests.proof.suites.ports.families import read
from trestle.common.plan import bounds

FAMILY = "compose"
UNKNOWN_IDENTIFIER = "admission.unknown_identifier"
BOUND_EXCEEDED = "admission.bound_exceeded"
COMPOSE_DEFINITION_INVALID = "adapter.compose_definition_invalid"
IDSET_MAX = 1024


def _closure(built: core.Implementation, project: str, *selected: str) -> Any:
    with read(built, "ComposeResolver.closure"):
        return built.impl.closure(project, frozenset(selected))


def closure_of_a_chain_is_its_transitive_dependencies(built: core.Implementation) -> None:
    result = _closure(built, built.extras["project"], "web")
    assert result.services == {"web", "api", "db"}
    assert result.edges == {("web", "api"), ("api", "db")}


def closure_of_a_service_without_dependencies_is_itself(built: core.Implementation) -> None:
    result = _closure(built, built.extras["project"], "cache")
    assert result.services == {"cache"} and result.edges == frozenset()


def closure_of_a_selection_is_the_union(built: core.Implementation) -> None:
    project = built.extras["project"]
    both = _closure(built, project, "web", "worker")
    assert both.services == {"web", "api", "db", "worker"}
    assert both.edges == {("web", "api"), ("api", "db"), ("worker", "db")}
    web, worker = _closure(built, project, "web"), _closure(built, project, "worker")
    assert both.services == web.services | worker.services
    assert both.edges == web.edges | worker.edges


def closure_is_equal_across_calls_and_selections_share_a_fingerprint(
    built: core.Implementation,
) -> None:
    project = built.extras["project"]
    first = _closure(built, project, "web", "worker")
    assert _closure(built, project, "worker", "web") == first  # order of selection is nothing
    other = _closure(built, project, "cache")
    assert other.definition_fingerprint == first.definition_fingerprint  # the definition's own
    assert len(first.definition_fingerprint) <= bounds.TOKEN_MAX and first.definition_fingerprint


def fingerprint_changes_with_the_definition(built: core.Implementation) -> None:
    before = _closure(built, built.extras["project"], "web")
    after = _closure(built, built.extras["changed_project"], "web")
    assert after.definition_fingerprint != before.definition_fingerprint
    assert after.services == {"web", "api", "db", "cache"}  # the new dependency is in the closure


def an_unknown_name_is_refused_and_named(built: core.Implementation) -> None:
    refused = _closure(built, built.extras["project"], "web", "no-such-service")
    assert refused.code == UNKNOWN_IDENTIFIER
    assert "no-such-service" in refused.subject and len(refused.subject) <= bounds.TEXT_MAX
    assert not hasattr(refused, "services")  # no partial closure


def an_unreadable_definition_is_refused(built: core.Implementation) -> None:
    refused = _closure(built, built.extras["invalid_project"], "web")
    assert refused.code == COMPOSE_DEFINITION_INVALID
    assert not hasattr(refused, "services")
    missing = _closure(built, "no-such-project", "web")
    assert missing.code == COMPOSE_DEFINITION_INVALID


def a_closure_over_the_bound_is_refused(built: core.Implementation) -> None:
    refused = _closure(built, built.extras["oversized_project"], "s0")
    assert refused.code == BOUND_EXCEEDED
    assert not hasattr(refused, "services")


CASES: Sequence[core.Case] = (
    core.Case(
        "closure_of_a_chain_is_its_transitive_dependencies",
        closure_of_a_chain_is_its_transitive_dependencies,
    ),
    core.Case(
        "closure_of_a_service_without_dependencies_is_itself",
        closure_of_a_service_without_dependencies_is_itself,
    ),
    core.Case("closure_of_a_selection_is_the_union", closure_of_a_selection_is_the_union),
    core.Case(
        "closure_is_equal_across_calls_and_selections_share_a_fingerprint",
        closure_is_equal_across_calls_and_selections_share_a_fingerprint,
    ),
    core.Case("fingerprint_changes_with_the_definition", fingerprint_changes_with_the_definition),
    core.Case("an_unknown_name_is_refused_and_named", an_unknown_name_is_refused_and_named),
    core.Case("an_unreadable_definition_is_refused", an_unreadable_definition_is_refused),
    core.Case("a_closure_over_the_bound_is_refused", a_closure_over_the_bound_is_refused),
)

core.register_family(FAMILY, CASES)
