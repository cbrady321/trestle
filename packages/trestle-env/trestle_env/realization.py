"""Realization spaces and root-entry levels, declared as data (MC-B-09; L.NW-3.2; V-7, V-8 L-7).

`realization_space(catalog, logical)` is the space of ways one logical backend system can be
realized, in declared preference order: its Docker service first, then one agent-launched project
per catalog override that replaces it. Every member carries the readiness contract the whole space
shares (V-7 `readiness`: it belongs to the LOGICAL system, so a local realization is judged by the
same contract the Docker node declared) and the vantages it declares itself reachable from (a
container consumer cannot reach a process on the host, so an override is `HOST` only; admission and
the endpoint read check the declaration, V-7.3). An externally managed realization needs a
`human_action` the catalog does not carry (`OQ-25`, undecided), so the reference space declares
none.

There is NO selection code here (hld-wr-environment KDD 4): choosing among the members is
`wr-workflow-runtime`'s, within the space `wr-run-control` validated at admission. A member's own
dependency subtree is what `closure()` derives for it: an override keeps the dependencies of the
node it replaces.

`choice_for(catalog, logical, docker_unit, readiness)` turns that space into the `ChoiceDeclaration`
a tree publishes for the logical system (L.RB-8.2): the Docker node's unit first, then one
agent-launched alternative per override, whose unit name IS the override identifier (a request names
an override in `overrides`, the choice's `select_arg`, and only the alternatives it names are
eligible). Admission then refuses, before a run id, a request whose selected alternative does not
declare the vantage a dependent consumes it from (a container consumer of a local process:
`ROUTE_UNSUPPORTED`, V-7.3). Nothing here selects: the selection is `wr-run-control`'s.

Root-entry levels (OQ-31 is open) are declared under both readings and parameterized by reading:
`PRECONDITIONS_COVERED` (the interface stage's: a unit is a root only if its preconditions are
covered by its own children's `needs` or by checks its own leaves observe) and `ENV_KEY_FIELD`
(the design's converged rule: a root that imports the environment ports names `env_key_field`).
The two composites the reference tree publishes are eligible under both; the bare `run_selector`
leaf is eligible under neither (its preconditions name checks no child of its own covers).

Stdlib and `trestle.workflow` only (root C.5 step 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trestle.workflow.declarations import Alternative, ChoiceDeclaration, RealizationKind, Vantage

from trestle_env.catalog import Catalog, OverrideId, ProjectId, ServiceId, TaskId
from trestle_env.schema import OVERRIDES_ARG

ENV_AND_TEST = "env_and_test"  # executor-chosen level names (interface-work-unit P1-1/P1-2)
SYSTEM_TEST = "system_test"
RUN_SELECTOR = "run_selector"


@dataclass(frozen=True)
class Realization:
    """One declared way to realize a logical system. Data only."""

    logical_system: ServiceId
    kind: RealizationKind
    reachable_from: frozenset[Vantage]
    readiness: ServiceId  # the logical system whose readiness contract judges every member
    override: OverrideId | None = None
    project: ProjectId | None = None  # the local project the run launches (AGENT_LAUNCHED_PROJECT)
    task: TaskId | None = None
    human_action: str | None = None  # required for EXTERNALLY_MANAGED (V-7); none is declared


DOCKER_REACH = frozenset({Vantage.HOST, Vantage.CONTAINER})
LOCAL_REACH = frozenset({Vantage.HOST})


def realization_space(catalog: Catalog, logical: str) -> tuple[Realization, ...]:
    """The declared space of `logical`, in preference order; empty for an identifier the catalog
    does not hold (the caller refuses the identifier before it asks for a space)."""
    service = catalog.service(logical)
    if service is None:
        return ()
    space = [Realization(service.id, RealizationKind.DOCKER_SERVICE, DOCKER_REACH, service.id)]
    space.extend(
        Realization(
            service.id,
            RealizationKind.AGENT_LAUNCHED_PROJECT,
            LOCAL_REACH,
            service.id,
            override=o.id,
            project=o.project,
            task=o.task,
        )
        for o in catalog.overrides
        if o.service == service.id
    )
    return tuple(space)


def choice_for(
    catalog: Catalog, logical: str, docker_unit: str, readiness: str
) -> ChoiceDeclaration:
    """The V-7 choice of `logical`'s realization space: `docker_unit` (the Docker service, the
    fallback) and one alternative per override, unit = the override id, reachable from the host
    only. Refuses (`ValueError`) a system the catalog does not hold."""
    space = realization_space(catalog, logical)
    if not space:
        raise ValueError(f"{logical!r} is not a catalog service")
    alternatives = tuple(
        Alternative(
            docker_unit if member.override is None else str(member.override),
            member.kind,
            member.reachable_from,
            member.human_action,
        )
        for member in space
    )
    return ChoiceDeclaration(logical, alternatives, OVERRIDES_ARG, docker_unit, readiness)


class RootEntryReading(StrEnum):
    """The two readings of root-entry eligibility OQ-31 leaves open (V-8 L-7)."""

    PRECONDITIONS_COVERED = "preconditions_covered"
    ENV_KEY_FIELD = "env_key_field"


@dataclass(frozen=True)
class RootEntryLevel:
    level: str
    eligible_under: frozenset[RootEntryReading]


BOTH = frozenset(RootEntryReading)

ROOT_ENTRY_LEVELS: tuple[RootEntryLevel, ...] = (
    RootEntryLevel(ENV_AND_TEST, BOTH),
    RootEntryLevel(SYSTEM_TEST, BOTH),
    RootEntryLevel(RUN_SELECTOR, frozenset()),
)


def root_entry_levels(reading: RootEntryReading | None = None) -> tuple[str, ...]:
    """The levels eligible as root entries under `reading`; with `None`, the levels eligible under
    every reading (the fixtures both readings must accept)."""
    wanted = BOTH if reading is None else frozenset({reading})
    return tuple(
        level.level for level in ROOT_ENTRY_LEVELS if level.eligible_under >= wanted and wanted
    )
