"""The reference environment tree, declared as data (MC-B-04 v0; L.RB-0.2; WR-ENV-10, WR-UNIT-8).

`ENTRY` is the `WorkflowEntry` the `reference_env` plugin (plugins/reference_env.py) binds: a root
all-of declaration `reference_env` over one child, `backend.postgres`, a leaf whose one realization
is a Docker service and whose readiness is an authenticated `SELECT 1` read through
`ResourceReads.check`. Composites are data and the loop belongs to the workflow package
(hld-wr-environment KDD 4): nothing here selects, orders or retries; the units only observe and
issue the effects they declare.

* The environment key is the request's `env` argument, the Compose project name; the root declares
  it as `env_key_field` and the host compares it only as opaque bytes (KDD 1).
* Readiness is never a sleep, a port being open or `pg_isready` (KDD 2). `POSTGRES_READINESS` is
  the authenticated call: `psql` over TCP to the container's OWN non-loopback address. The official
  image's `initdb` trusts local sockets and 127.0.0.1/::1 without a password and only its appended
  `host all all all scram-sha-256` line demands one, so a loopback or socket check would pass
  with a wrong password; the fixture never sets `POSTGRES_HOST_AUTH_METHOD`. The password travels
  in the exec environment (`PGPASSWORD`), never in the argv. Whether authentication really happens
  is proven at HOST by L.RB-0.4's planted wrong-password case; here only the argv shape is data.
* The unit names the logical service, never a container: the run-scoped selector
  `trwr-<root run_id>-<path>` comes from the port (MC-B-01) and the created container is released
  through the `ArgvRelease` descriptor the port records with the create ticket.

This module imports the standard library and `trestle.workflow` only (root C.5 step 4): a concrete
adapter is bound by the composition root (`plugins/reference_env.py`), never here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final

from trestle.workflow import (
    AllDeclaration,
    ArgBinding,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
from trestle.workflow.units import ActContext, Acted, EffectFacets, ObserveContext, ReadFacets, Step
from trestle.workflow.values import CheckResult, CreatedHandle, Observation, Verdict

from trestle_env.catalog import Catalog, load_reference
from trestle_env.schema import ENV_ARG, OVERRIDES_ARG, SERVICES_ARG, TESTS_ARG

ROOT_UNIT: Final = "reference_env"
POSTGRES_UNIT: Final = "backend.postgres"
POSTGRES_SERVICE: Final = "postgres"  # the catalog identifier and the logical system
POSTGRES_ROLE: Final = "postgres"  # the MC-B-10 image role the composition root resolves
SERVICES_SET: Final = "services"  # the declared identifier sets the request arguments name
TESTS_SET: Final = "tests"
OVERRIDES_SET: Final = "overrides"

# Declared effects of a Docker-service leaf (V-14): one run-lifetime create and its owned release.
UP: Final = "up"
STOP: Final = "stop"

# The fixture Postgres identity. Fixture values for the proof stack only (never a real credential:
# the container is created with them and destroyed with the run).
POSTGRES_USER: Final = "trestle"
POSTGRES_DATABASE: Final = "trestle"
POSTGRES_FIXTURE_PASSWORD: Final = "trestle-fixture-password"

# Declared timings, in seconds. The wait is the readiness stage's own budget (hld-wr-environment
# Provisioning & System Test: a stage that never passes ends at its declared wait); the leaf budget
# holds the wait plus the release timeout (B2-C5); every level leaves the reserve the carve
# demands, and the plugin's deadline holds the root budget plus the release slice. The release
# timeout bounds each descriptor command (observe, stop, remove) and is small on purpose: admission
# refuses a root whose worst-case finalization, `grace + kill + 5 * release_timeout` per release
# rank (B2-C2 (5)), exceeds the operator's finalization margin (35 s by default), so every rank of
# create-run effects a tree declares costs `5 * RELEASE_TIMEOUT_S` of that margin.
DEADLINE_S: Final = 180
ROOT_BUDGET_S: Final = 150
LEAF_BUDGET_S: Final = 120
READY_POLL_S: Final = 1
READY_WAIT_S: Final = 60
RELEASE_TIMEOUT_S: Final = 2
CONCURRENCY: Final = 2


@dataclass(frozen=True)
class ExecReadiness:
    """A readiness check run inside the container by `docker exec` (exit 0 satisfies it).

    Data only: the composition root binds it to the container adapter's exec-check registry. The
    `environment` values are passed to the exec, never placed in `argv`."""

    check: str
    argv: tuple[str, ...]
    environment: Mapping[str, str] = field(default_factory=dict)


POSTGRES_READY: Final = "postgres_ready"

POSTGRES_READINESS: Final = ExecReadiness(
    check=POSTGRES_READY,
    argv=(
        "sh",
        "-c",
        'exec psql -h "$(hostname -i | cut -d" " -f1)"'
        f' -U {POSTGRES_USER} -d {POSTGRES_DATABASE} -tAc "SELECT 1"',
    ),
    environment={"PGPASSWORD": POSTGRES_FIXTURE_PASSWORD},
)
"""The authenticated call that makes Postgres ready (KDD 2)."""

READINESS: Final[Mapping[str, ExecReadiness]] = {POSTGRES_READY: POSTGRES_READINESS}
"""Every exec readiness check the tree declares, by check id."""


class DockerServiceUnit:
    """A leaf whose one realization is a Docker service: created by this run, ready when its
    declared readiness check passes, released (stopped and removed, never a volume) with the run.

    One instance serves one logical service; it holds no state (the record is the loop's)."""

    def __init__(self, unit: str, service: str, readiness: str) -> None:
        self._unit = unit
        self._readiness = readiness
        # the entry is the catalog identifier the port maps to the container definition
        self._spec = ResourceSpec(service, RealizationKind.DOCKER_SERVICE, service, None)

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition=self._readiness,
            wait=WaitPolicy(timedelta(seconds=READY_POLL_S), 1.0, timedelta(seconds=READY_WAIT_S)),
            resource_kind="docker_container",
            may_touch=frozenset({"docker_container"}),
            effects=(
                EffectDeclaration(
                    UP,
                    EffectFacetClass.CREATE,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=RELEASE_TIMEOUT_S),
                ),
                EffectDeclaration(
                    STOP,
                    EffectFacetClass.OWNED,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=RELEASE_TIMEOUT_S),
                    is_release=True,
                ),
            ),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=LEAF_BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, UP)
        ready = CheckResult(False, None, "")
        code = seen.code
        if seen.selector_ref is not None and code is None:
            # the authoritative observation: the declared authenticated call, never a convenience
            # read that can lag behind the write (KDD 2)
            ready = resource.check(self._readiness, seen.selector_ref)
            code = ready.code
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=ready,
            preconditions=(),
            currency=seen.currency,
            found=tuple(seen.found),
            code=code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        effects.create(ResourceCreate).create(self._spec, UP)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP)
        return Acted()


CATALOG: Final[Catalog] = load_reference()
"""The trusted catalog the tree's identifier sets are drawn from (`catalog/reference.json`)."""


def identifier_sets(catalog: Catalog) -> dict[str, frozenset[str]]:
    """The identifier set each request argument is bound to, from the catalog."""
    return {
        SERVICES_SET: frozenset(str(s.id) for s in catalog.services),
        TESTS_SET: frozenset(str(t.id) for t in catalog.tests),
        OVERRIDES_SET: frozenset(str(o.id) for o in catalog.overrides),
    }


ENTRY = WorkflowEntry(
    root=ROOT_UNIT,
    units={
        ROOT_UNIT: AllDeclaration(
            unit=ROOT_UNIT,
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(ChildBinding(unit=POSTGRES_UNIT, params={}, needs=()),),
            concurrency=CONCURRENCY,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            # what a request may name: admission refuses any other identifier before a run id
            # (B2-C2 (1)) with UNKNOWN_IDENTIFIER, naming it and where the valid ones are listed
            identifier_sets=identifier_sets(CATALOG),
            arg_bindings=(
                ArgBinding(SERVICES_ARG, SERVICES_SET, False),
                ArgBinding(TESTS_ARG, TESTS_SET, False),
                ArgBinding(OVERRIDES_ARG, OVERRIDES_SET, False),
            ),
            env_key_field=ENV_ARG,
        ),
        POSTGRES_UNIT: DockerServiceUnit(POSTGRES_UNIT, POSTGRES_SERVICE, POSTGRES_READY),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)
"""The reference tree at v0: `reference_env` -> `backend.postgres`."""
