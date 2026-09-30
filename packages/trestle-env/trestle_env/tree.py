"""The reference environment tree, declared as data (MC-B-04; L.RB-0.2, L.RB-2.1; WR-ENV-10).

`ENTRY` is the `WorkflowEntry` the `reference_env` plugin (plugins/reference_env.py) binds: a root
all-of declaration `reference_env` over two leaves whose realization is a Docker service. Each is
ready only when its own declared check passes, read through `ResourceReads.check`:
`backend.http_support` when its declared endpoint answers its declared response, and
`backend.postgres`, which needs it, when an authenticated `SELECT 1` succeeds. Composites are data
and the loop belongs to the workflow package (hld-wr-environment KDD 4): nothing here selects,
orders or retries; the units only observe and issue the effects they declare, and the `needs`
edge is the loop's gate (WR-VERIFY-2): a dependent starts only after its dependency's readiness
pass, never after a sleep or an open port.

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
HTTP_SUPPORT_UNIT: Final = "backend.http_support"
HTTP_SUPPORT_SERVICE: Final = "http_support"
HTTP_SUPPORT_ROLE: Final = "http_support"
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
# demands, the root budget holds the longest `needs` chain of leaf budgets (two leaves, one after
# the other), and the plugin's deadline holds the root budget plus the release slice. The release
# timeout bounds each descriptor command (observe, stop, remove) and is small on purpose: admission
# refuses a root whose worst-case finalization, `grace + kill + 5 * release_timeout` per release
# rank (B2-C2 (5)), exceeds the operator's finalization margin (35 s by default), so every rank of
# create-run effects a tree declares costs `5 * RELEASE_TIMEOUT_S` of that margin.
DEADLINE_S: Final = 120
ROOT_BUDGET_S: Final = 100
LEAF_BUDGET_S: Final = 40
READY_POLL_S: Final = 1
READY_WAIT_S: Final = 30
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


@dataclass(frozen=True)
class HttpReadiness:
    """A readiness contract over HTTP: a GET of `path` on the service's host-reachable endpoint
    answers `status` with exactly `body`. The service is ready when, and only when, the declared
    response comes back; a refused connection, another status or another body is not ready yet.

    Data only: the composition root binds it to a read facet that makes the request (the unit
    itself starts nothing and opens no socket)."""

    check: str
    path: str
    status: int
    body: str


HTTP_SUPPORT_READY: Final = "http_support_ready"

HTTP_SUPPORT_READINESS: Final = HttpReadiness(HTTP_SUPPORT_READY, "/health", 200, "ok")
"""The declared endpoint and response that make the supporting service ready."""

HTTP_READINESS: Final[Mapping[str, HttpReadiness]] = {HTTP_SUPPORT_READY: HTTP_SUPPORT_READINESS}
"""Every HTTP readiness contract the tree declares, by check id."""


RESOURCE_KINDS: Final[Mapping[RealizationKind, str]] = {
    RealizationKind.DOCKER_SERVICE: "docker_container",
    RealizationKind.AGENT_LAUNCHED_PROJECT: "local_process",
}
"""The resource kind a leaf declares for the realization its spec names (V-14 `resource_kind`)."""


class ServiceUnit:
    """A leaf whose one resource is created by this run, ready when its declared readiness check
    passes, and released (stopped and removed, never a volume) with the run.

    One instance serves one logical service; it holds no state (the record is the loop's). The
    resource is a Docker service by default; a proof of the readiness contract on a local process
    gives the same unit an agent-launched spec, so the contract is the unit's and the realization
    is the port's."""

    def __init__(
        self, unit: str, service: str, readiness: str, spec: ResourceSpec | None = None
    ) -> None:
        self._unit = unit
        self._readiness = readiness
        # the entry is the catalog identifier the port maps to the container definition
        self._spec = (
            spec
            if spec is not None
            else ResourceSpec(service, RealizationKind.DOCKER_SERVICE, service, None)
        )

    def declare(self) -> LeafDeclaration:
        kind = RESOURCE_KINDS[self._spec.realization]
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition=self._readiness,
            wait=WaitPolicy(timedelta(seconds=READY_POLL_S), 1.0, timedelta(seconds=READY_WAIT_S)),
            resource_kind=kind,
            may_touch=frozenset({kind}),
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
            children=(
                # the supporting service first: the backend starts only after its readiness pass
                ChildBinding(unit=HTTP_SUPPORT_UNIT, params={}, needs=()),
                ChildBinding(unit=POSTGRES_UNIT, params={}, needs=(HTTP_SUPPORT_UNIT,)),
            ),
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
        HTTP_SUPPORT_UNIT: ServiceUnit(HTTP_SUPPORT_UNIT, HTTP_SUPPORT_SERVICE, HTTP_SUPPORT_READY),
        POSTGRES_UNIT: ServiceUnit(POSTGRES_UNIT, POSTGRES_SERVICE, POSTGRES_READY),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)
"""The reference tree at v2: `reference_env` -> `backend.http_support` -> `backend.postgres`."""
