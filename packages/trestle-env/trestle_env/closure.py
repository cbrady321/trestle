"""Closure derivation over `ComposeResolver` output, with local-override substitution
(MC-B-09; L.NW-3.2; B3-C13, WR-ENV-1, WR-ENV-2, WR-ENV-12).

`closure(catalog, compose, selected, overrides)` takes the `Closure` a `ComposeResolver` returned
for `selected` (or its `ClosureRefused`, which passes through UNCHANGED: same V-11 code, its
subject as the `identifier`) and returns a `ClosurePlan`, or a `Refused(code, identifier)`:

* every service in the closure must be a catalog service (the catalog is never widened by a
  Compose definition: `UNKNOWN_IDENTIFIER`, naming it); every selected name must be in the closure
  (else `UNKNOWN_IDENTIFIER`, the definition does not name it); a selection over `SELECT_MAX` is
  `BOUND_EXCEEDED` (WR-ENV-12: an answer selects at most 100);
* a `ClosurePlan` node is a service with the services it needs (its dependencies inside the
  closure, from the Compose edges), the realization it runs as, and the readiness contract judging
  it (the LOGICAL system's, shared by every realization, V-7);
* an override replaces its service's Docker node with an agent-launched local node that KEEPS the
  replaced node's `needs` and its readiness contract, and dependents keep pointing at it
  (WR-ENV-2); no Docker node remains for that service. An override whose service is not in the
  closure changes nothing; an unknown override id is `UNKNOWN_IDENTIFIER`; two overrides for one
  service are `admission.invalid_args` naming the service (OQ-25: multiple local overrides of one
  node are not promoted).

The plan carries no order: sequencing within the closure is the loop's, never derived here.

Stdlib and `trestle.workflow` only (root C.5 step 4). The codes are spelled as the boundary raises
them (`trestle_env.codes` says why they are not redefined).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from trestle.workflow.declarations import RealizationKind
from trestle.workflow.ports import Closure, ClosureRefused

from trestle_env.catalog import Catalog, Override, OverrideId, ProjectId, ServiceId, TaskId

UNKNOWN_IDENTIFIER: Final = "admission.unknown_identifier"
BOUND_EXCEEDED: Final = "admission.bound_exceeded"
INVALID_ARGS: Final = "admission.invalid_args"
SELECT_MAX: Final = 100  # WR-ENV-12 (hld-wr-environment): an answer selects at most 100 services


@dataclass(frozen=True)
class Refused:
    code: str
    identifier: str


@dataclass(frozen=True)
class ClosureNode:
    service: ServiceId
    realization: RealizationKind
    needs: frozenset[ServiceId]  # its dependencies inside the closure
    readiness: ServiceId  # the logical system whose readiness contract judges this node
    override: OverrideId | None = None
    project: ProjectId | None = None
    task: TaskId | None = None


@dataclass(frozen=True)
class ClosurePlan:
    nodes: tuple[ClosureNode, ...]  # sorted by service id; carries no execution order
    definition_fingerprint: str

    @property
    def services(self) -> frozenset[ServiceId]:
        return frozenset(n.service for n in self.nodes)

    def node(self, service: str) -> ClosureNode | None:
        return next((n for n in self.nodes if n.service == service), None)


def closure(
    catalog: Catalog,
    compose: Closure | ClosureRefused,
    selected: Iterable[str],
    overrides: Iterable[str] = (),
) -> ClosurePlan | Refused:
    if isinstance(compose, ClosureRefused):
        return Refused(compose.code, compose.subject)
    chosen = sorted(set(map(str, selected)))
    if len(chosen) > SELECT_MAX:
        return Refused(BOUND_EXCEEDED, f"{len(chosen)} services selected, at most {SELECT_MAX}")
    for name in chosen:
        if name not in compose.services:
            return Refused(UNKNOWN_IDENTIFIER, name)
    for name in sorted(compose.services):
        if catalog.service(name) is None:
            return Refused(UNKNOWN_IDENTIFIER, name)
    replaced = _overrides(catalog, compose, overrides)
    if isinstance(replaced, Refused):
        return replaced
    nodes = []
    for name in sorted(compose.services):
        service = catalog.service(name)
        assert service is not None  # checked above
        needs = frozenset(
            ServiceId(dep) for owner, dep in compose.edges if owner == name and dep != name
        )
        local = replaced.get(name)
        if local is None:
            nodes.append(ClosureNode(service.id, RealizationKind.DOCKER_SERVICE, needs, service.id))
        else:
            nodes.append(
                ClosureNode(
                    service.id,
                    RealizationKind.AGENT_LAUNCHED_PROJECT,
                    needs,  # the replaced node's dependencies survive the substitution
                    service.id,  # and so does its readiness contract
                    local.id,
                    local.project,
                    local.task,
                )
            )
    return ClosurePlan(tuple(nodes), compose.definition_fingerprint)


def _overrides(
    catalog: Catalog, compose: Closure, ids: Iterable[str]
) -> dict[str, Override] | Refused:
    replaced: dict[str, Override] = {}
    for identifier in sorted(set(map(str, ids))):
        override = catalog.override(identifier)
        if override is None:
            return Refused(UNKNOWN_IDENTIFIER, identifier)
        if override.service not in compose.services:
            continue
        if override.service in replaced:
            return Refused(INVALID_ARGS, str(override.service))
        replaced[str(override.service)] = override
    return replaced
