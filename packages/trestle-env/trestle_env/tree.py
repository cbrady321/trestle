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

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
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
from trestle.workflow.ports import (
    BoundCommand,
    ExecutionPort,
    Resolved,
    ResourceCreate,
    ResourceOwned,
    ResourceReads,
    ResourceSpec,
    ToolchainResolver,
    Unresolved,
)
from trestle.workflow.units import (
    ActContext,
    Acted,
    Blocked,
    EffectFacets,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import CheckResult, CreatedHandle, Observation, Resend, Verdict

from trestle_env.catalog import Catalog, load_reference
from trestle_env.catalog.model import Project, TaskEntry, TestSpec
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
# The work nodes after readiness (the provisioning submit, a test's task) have their own, shorter
# budget: the root budget holds the longest `needs` chain, backend -> provision -> test.
STAGE_BUDGET_S: Final = 20
STAGE_WAIT_S: Final = 10
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

# Reuse proof (WR-OWN-7, KDD 3): a found Postgres is reused only on PROVEN identity and
# configuration, and only when it is ready. Both proofs are read from inside the found container
# and need no running server, so a container whose server is down still has its identity proven
# (and is then unhealthy, not foreign):
#   identity      - it was created for the reference stack: the fixture role and database in its
#                   own environment (the values the run itself creates its container with);
#   configuration - it runs the Postgres major version the reference image pins.
POSTGRES_IDENTITY: Final = "postgres_identity"
POSTGRES_CONFIGURATION: Final = "postgres_configuration"
POSTGRES_MAJOR: Final = 16

POSTGRES_IDENTITY_PROOF: Final = ExecReadiness(
    check=POSTGRES_IDENTITY,
    argv=(
        "sh",
        "-c",
        f'test "$POSTGRES_USER" = {POSTGRES_USER} && test "$POSTGRES_DB" = {POSTGRES_DATABASE}',
    ),
)
POSTGRES_CONFIGURATION_PROOF: Final = ExecReadiness(
    check=POSTGRES_CONFIGURATION,
    argv=("sh", "-c", f'postgres --version | grep -q "(PostgreSQL) {POSTGRES_MAJOR}\\."'),
)

EXEC_CHECKS: Final[Mapping[str, ExecReadiness]] = {
    **READINESS,
    POSTGRES_IDENTITY: POSTGRES_IDENTITY_PROOF,
    POSTGRES_CONFIGURATION: POSTGRES_CONFIGURATION_PROOF,
}
"""Every exec check the tree declares (readiness and reuse proof), bound by the composition root."""


@dataclass(frozen=True)
class ReuseProof:
    """The checks that prove a FOUND resource is this service's: its identity and its
    configuration (check ids, read from inside the found resource). Declared by a unit that
    may reuse what it finds; a unit that declares none never does (a found resource is then
    incompatible, WR-OWN-7)."""

    identity: str
    configuration: str


POSTGRES_REUSE: Final = ReuseProof(POSTGRES_IDENTITY, POSTGRES_CONFIGURATION)


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
        self,
        unit: str,
        service: str,
        readiness: str,
        spec: ResourceSpec | None = None,
        reuse: ReuseProof | None = None,
    ) -> None:
        self._unit = unit
        self._readiness = readiness
        self._reuse = reuse
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
        identity, configuration = seen.identity_proven, seen.configuration_compatible
        if seen.selector_ref is not None and code is None:
            # the authoritative observation: the declared authenticated call, never a convenience
            # read that can lag behind the write (KDD 2)
            ready = resource.check(self._readiness, seen.selector_ref)
            code = ready.code
        elif seen.found and code is None and self._reuse is not None:
            # a found resource of this service: reused only on proven identity and configuration,
            # and only when its own readiness passes; the proofs are read, never assumed. It is
            # never created over, stopped, adopted or released (V-4.2).
            found = seen.found[0]
            proofs = (
                resource.check(self._reuse.identity, found),
                resource.check(self._reuse.configuration, found),
                resource.check(self._readiness, found),
            )
            code = next((c.code for c in proofs if c.code is not None), None)
            identity, configuration, ready = (
                proofs[0].satisfied,
                proofs[1].satisfied,
                proofs[2],
            )
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=identity,
            configuration_compatible=configuration,
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


CATALOG_ENV: Final = "TRESTLE_ENV_CATALOG"


def configured_catalog(environ: Mapping[str, str]) -> Catalog:
    """The trusted catalog: the operator's (`TRESTLE_ENV_CATALOG`, an absolute path to a catalog
    file) or the reference one that ships with the package. The catalog is the operator's data, so
    which tests exist, and so which test nodes the tree has, is the operator's to configure."""
    named = environ.get(CATALOG_ENV)
    if not named:
        return load_reference()
    if not os.path.isabs(named):
        raise ValueError(f"{CATALOG_ENV} must be an absolute path, got {named!r}")
    return Catalog.load(Path(named))


CATALOG: Final[Catalog] = configured_catalog(os.environ)
"""The trusted catalog the tree's identifier sets and test nodes are drawn from."""

TASK_PREFIX: Final = "test"  # a catalog test's node is `test.<test id>` (stages.py: the test stage)
TASK: Final = "task"  # the declared effect: one run of the allowlisted task
NOTHING: Final = ""  # the `BoundCommand.task` of "no test was requested": a run of nothing
FAILING_EVIDENCE: Final = "test.failing"  # the evidence event that names a failed run's test ids


def task_unit_name(test_id: str) -> str:
    return f"{TASK_PREFIX}.{test_id}"


PROVISION_UNIT: Final = "provision.postgres"
PROVISION_SERVICE: Final = "postgres"  # the logical system whose record is provisioned
PROVISION_ENTRY: Final = "reference-fixture"  # the catalog entry the store maps to its payload
PROVISION_PAYLOAD: Final = "fixture-record"
SUBMIT: Final = "submit"
PROVISIONED: Final = "provisioned"  # the postcondition: the authoritative probe sees the record


class ProvisionUnit:
    """Provision the environment's fixture record exactly once (L.RB-6.2; WR-ENV-4, B3.3).

    The submit is `ResourceCreate.create` of a `PROVISIONED` resource and NOT safe to resubmit
    (`Repeat.ONCE`): once a ticket is issued the node never issues another, whatever a read says.
    Completion is observed through the AUTHORITATIVE probe only (`ResourceReads.observe`, a
    read-only authenticated query of the record store): a submit the port ACCEPTED is not the
    record being there, so the node converges (polls, within its declared wait) until the probe
    sees it, and a lagging convenience read (`check`) is never consulted. A record of the same
    system already in the store under another key (an earlier equivalent run's) is reused: no
    submit is issued for it. The record is durable (`Durable(ENVIRONMENT)`): no run releases it."""

    def __init__(self) -> None:
        self._spec = ResourceSpec(
            PROVISION_SERVICE, RealizationKind.PROVISIONED, PROVISION_ENTRY, None
        )

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=PROVISION_UNIT,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.ONCE),
            preconditions=(),
            postcondition=PROVISIONED,
            wait=WaitPolicy(timedelta(seconds=READY_POLL_S), 1.0, timedelta(seconds=STAGE_WAIT_S)),
            resource_kind="postgres_record",
            may_touch=frozenset({"postgres_record"}),
            effects=(
                EffectDeclaration(
                    SUBMIT, EffectFacetClass.CREATE, "", Lifetime.DURABLE, frozenset(), None
                ),
            ),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=STAGE_BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        seen = reads.read(ResourceReads).observe(self._spec, ctx.lineage, SUBMIT)
        recorded = seen.selector_present or bool(seen.found)
        return Observation(
            present=recorded,
            selector_present=seen.selector_present,
            # a found record was selected by the system it names: it is this service's record
            identity_proven=seen.identity_proven or bool(seen.found),
            configuration_compatible=True,
            postcondition=CheckResult(recorded and seen.code is None, seen.code, ""),
            preconditions=(),
            currency=(),
            found=tuple(seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        effects.create(ResourceCreate).create(self._spec, SUBMIT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        raise AssertionError("a durable record is never released by a run")


class TaskUnit:
    """The toolchain leg for one catalog test: run its project's allowlisted task once, when the
    request names the test, through the `ExecutionPort` (L.RB-4.5; B3-C14, WR-ENV-3, WR-ENV-15).

    The task is the catalog's data: `argv[0]` a bare tool name the `ToolchainResolver` resolves to
    an absolute executable EXACTLY (the project's declared pin), the rest literals; nothing of the
    request but the test identifier reaches a command. A pin that does not resolve ends the node
    BLOCKED with that resolution's own code and human action BEFORE any effect is issued, so no
    task-start record can exist (nothing installs the tool, OQ-18). A test the request did not
    name is a run of nothing (`NOTHING`): recorded as passed with no command, so the node is
    always in the tree and always ends. Completion is RECORDED: the recorded result decides."""

    def __init__(self, test: TestSpec, project: Project, task: TaskEntry) -> None:
        self._unit = task_unit_name(str(test.id))
        self._test = str(test.id)
        self._project = str(project.id)
        self._task = str(task.id)
        self._argv = tuple(str(a) for a in task.argv)
        self._reports = task.reports_tests

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.RECORDED, Repeat.SAFE),
            preconditions=(),
            postcondition="task_recorded",
            wait=WaitPolicy(timedelta(seconds=READY_POLL_S), 1.0, timedelta(seconds=STAGE_WAIT_S)),
            resource_kind="toolchain_task",
            may_touch=frozenset({"toolchain_task"}),
            effects=(
                EffectDeclaration(
                    TASK, EffectFacetClass.EVENT, "", Lifetime.RUN, frozenset(), None
                ),
            ),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=STAGE_BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        return Observation(
            present=False,
            selector_present=False,
            identity_proven=False,
            configuration_compatible=True,
            postcondition=CheckResult(False, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def _requested(self, params: Any) -> bool:
        named = params.get("tests") if isinstance(params, Mapping) else None
        return isinstance(named, list) and self._test in named

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if not self._requested(params):
            command = _nothing()
        else:
            tool, *rest = self._argv
            resolved = effects.read(ToolchainResolver).resolve(self._project, tool)
            if isinstance(resolved, Unresolved):
                return Blocked(resolved.code, resolved.human_action, Resend.SUCCEEDS_AFTER_ACTION)
            command = BoundCommand(
                task=f"{self._project}/{self._task}",
                argv=(resolved.executable, *rest),
                environment={},
                resolved=resolved,
                reports_tests=self._reports,
            )
        _, result = effects.event(ExecutionPort).run(
            command, TASK, ctx.cancellation, ctx.clock.release_point
        )
        if result is not None and not result.recorded.passed and result.failing:
            # the failing test ids ride the run's evidence (the answer's `detail` handle reaches
            # it, B4-C5); the node's class and code stay the recorded result's
            ctx.evidence.event(
                FAILING_EVIDENCE,
                {"test": self._test, "failing": list(result.failing), "code": result.code},
            )
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        raise AssertionError("a task creates nothing to release")


def _nothing() -> BoundCommand:
    return BoundCommand(NOTHING, (), {}, Resolved("", "", "", ""), False)


def identifier_sets(catalog: Catalog) -> dict[str, frozenset[str]]:
    """The identifier set each request argument is bound to, from the catalog."""
    return {
        SERVICES_SET: frozenset(str(s.id) for s in catalog.services),
        TESTS_SET: frozenset(str(t.id) for t in catalog.tests),
        OVERRIDES_SET: frozenset(str(o.id) for o in catalog.overrides),
    }


def build_entry(catalog: Catalog) -> WorkflowEntry:
    """The reference tree over `catalog`: the two backends (independent of each other), and one
    `test.<id>` node per catalog test that needs EVERY backend's readiness pass (WR-VERIFY-2: a
    test never starts before readiness). The tree is the catalog's data made declarations."""
    tests = catalog.tests

    def task_unit(test: TestSpec) -> TaskUnit:
        project = catalog.project(str(test.project))
        task = catalog.task(str(test.project), str(test.task))
        assert project is not None and task is not None  # the catalog checked its references
        return TaskUnit(test, project, task)

    backends = (HTTP_SUPPORT_UNIT, POSTGRES_UNIT)
    provisioned = any(t.provision for t in tests)  # a test that needs the fixture record
    return WorkflowEntry(
        root=ROOT_UNIT,
        units={
            ROOT_UNIT: AllDeclaration(
                unit=ROOT_UNIT,
                flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
                children=(
                    ChildBinding(unit=HTTP_SUPPORT_UNIT, params={}, needs=()),
                    ChildBinding(unit=POSTGRES_UNIT, params={}, needs=()),
                    *(
                        (ChildBinding(unit=PROVISION_UNIT, params={}, needs=(POSTGRES_UNIT,)),)
                        if provisioned
                        else ()
                    ),
                    # the test leg: one node per catalog test, after every readiness pass, running
                    # the test's project task when the request names the test
                    *(
                        ChildBinding(
                            unit=task_unit_name(str(t.id)),
                            params={"tests": TESTS_ARG},
                            needs=(*backends, *((PROVISION_UNIT,) if t.provision else ())),
                        )
                        for t in tests
                    ),
                ),
                concurrency=CONCURRENCY,
                budget=timedelta(seconds=ROOT_BUDGET_S),
                # what a request may name: admission refuses any other identifier before a run id
                # (B2-C2 (1)) with UNKNOWN_IDENTIFIER, naming it and where valid ones are listed
                identifier_sets=identifier_sets(catalog),
                arg_bindings=(
                    ArgBinding(SERVICES_ARG, SERVICES_SET, False),
                    ArgBinding(TESTS_ARG, TESTS_SET, False),
                    ArgBinding(OVERRIDES_ARG, OVERRIDES_SET, False),
                ),
                env_key_field=ENV_ARG,
            ),
            HTTP_SUPPORT_UNIT: ServiceUnit(
                HTTP_SUPPORT_UNIT, HTTP_SUPPORT_SERVICE, HTTP_SUPPORT_READY
            ),
            POSTGRES_UNIT: ServiceUnit(
                POSTGRES_UNIT, POSTGRES_SERVICE, POSTGRES_READY, reuse=POSTGRES_REUSE
            ),
            **({PROVISION_UNIT: ProvisionUnit()} if provisioned else {}),
            **{task_unit_name(str(t.id)): task_unit(t) for t in tests},
        },
        deadline=timedelta(seconds=DEADLINE_S),
    )


ENTRY = build_entry(CATALOG)
"""The reference tree over the operator's catalog: `reference_env` over `backend.http_support` and
`backend.postgres`, and one `test.<test id>` per catalog test, after both."""
