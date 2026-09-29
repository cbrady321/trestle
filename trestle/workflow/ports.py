"""Port Protocols the loop calls, the V-5 facet markers and the V-10 release descriptor forms
(L.SV-5.6; B3-C1..C6, B3-C14, B3-C21, V-5, V-10).

Stdlib and this package's own modules only (C.5 step 4): a port adapter, a fake or a real one,
implements these Protocols structurally and needs no `trestle` import beyond them (N7). Names are
B3's and the vocabulary's; nothing here is a second definition of a shape they state.

Read and effect facets are distinct types (B1-I3, V-5): a read Protocol derives from `ReadFacet`
alone and carries no effect member (B3-I2); an effect Protocol derives from one of the four effect
markers, every one of which inherits `EffectFacet.release_descriptor(call: EffectCall)` (V-5.4).
Every effect member takes an `AttemptTicket`; the unit-facing form (`facets.Ticketed`) substitutes
an effect id for it and issues the ticket first (B3-I3, B1-C6).

The three V-10 descriptor forms are stdlib frozen dataclasses under their names only
(`InRunGroup`, `ArgvRelease`, `Durable`). A port may hand back a form or its wire mapping (the
JSON object the attempt lane records: `form` = `in_run_group` | `argv` | `durable`); the loop
turns a mapping into exactly one form with `descriptor_from_wire`, which refuses a mapping that
matches none (so a fake needs no `trestle` import before SL-3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol, final

from trestle.workflow import codes
from trestle.workflow.declarations import (
    CheckRef,
    EffectId,
    HostScopeRef,
    HumanAction,
    Lifetime,
    RealizationKind,
    StableCode,
    Vantage,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    BoundedText,
    CancelSignal,
    CheckResult,
    Confirmation,
    CreatedHandle,
    CurrencyFact,
    FoundRef,
    Instant,
    Lineage,
    OwnedHandle,
    RecordedResult,
    SelectorRef,
    TestCounts,
)

type CatalogEntry = str  # trusted catalog id; never agent-supplied content (WR-AUTH-3)

# ==================== V-10 release descriptors


@final
@dataclass(frozen=True, slots=True)
class InRunGroup:
    """Released by ending every process attributable to the run (V-2.3). No command."""

    helpers_disclosed: bool = False  # the effect's ExecutionPolicy.helpers was DISCLOSED (B3-C3)


@final
@dataclass(frozen=True, slots=True)
class ArgvRelease:
    executable: str  # absolute path, resolved and recorded at ticket time
    observe_argv: tuple[str, ...]  # read-only presence check by the run-scoped selector
    observe_ok_exit: frozenset[int]  # exit statuses at which the engine answered (V-10.4)
    stop_argv: tuple[str, ...]  # owned-only graceful stop by the same selector; never a volume
    timeout: timedelta  # bounds each command; <= EffectCall.release_timeout (B3-C3)
    remove_argv: tuple[str, ...] | None = None  # runs only after stop_argv exits 0 (V-10)


class DurableOwner(StrEnum):
    HOST = "host"
    ENVIRONMENT = "environment"


@final
@dataclass(frozen=True, slots=True)
class Durable:
    owner: DurableOwner  # who keeps it; a run never releases it


type ReleaseDescriptor = InRunGroup | ArgvRelease | Durable

FORMS: tuple[str, ...] = ("in_run_group", "argv", "durable")


class DescriptorError(ValueError):
    """A wire mapping that is exactly none of the three V-10 forms."""


def _strs(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(i, str) for i in value):
        raise DescriptorError(f"{name} must be a list of strings")
    return tuple(value)


def descriptor_to_wire(descriptor: ReleaseDescriptor) -> dict[str, object]:
    """The wire mapping of a V-10 form (the JSON object the attempt lane records)."""
    if isinstance(descriptor, InRunGroup):
        return {"form": "in_run_group", "helpers_disclosed": descriptor.helpers_disclosed}
    if isinstance(descriptor, Durable):
        return {"form": "durable", "owner": DurableOwner(descriptor.owner).value}
    if isinstance(descriptor, ArgvRelease):
        remove = None if descriptor.remove_argv is None else list(descriptor.remove_argv)
        return {
            "form": "argv",
            "executable": descriptor.executable,
            "observe_argv": list(descriptor.observe_argv),
            "observe_ok_exit": sorted(descriptor.observe_ok_exit),
            "stop_argv": list(descriptor.stop_argv),
            "timeout_s": descriptor.timeout.total_seconds(),
            "remove_argv": remove,
        }
    raise DescriptorError(f"not a release descriptor: {type(descriptor).__name__}")


def descriptor_from_wire(wire: Mapping[str, object]) -> ReleaseDescriptor:
    """Exactly one V-10 form from its wire mapping, or `DescriptorError` (nothing guessed): the
    form name must be one of `FORMS`, the key set must be the form's own, and every value must
    have the form's type."""
    if not isinstance(wire, Mapping):
        raise DescriptorError(
            f"a release descriptor mapping is required, got {type(wire).__name__}"
        )
    form = wire.get("form")
    keys = set(wire) - {"form"}
    if form == "in_run_group":
        if keys != {"helpers_disclosed"} or not isinstance(wire["helpers_disclosed"], bool):
            raise DescriptorError("in_run_group takes exactly helpers_disclosed: bool")
        return InRunGroup(helpers_disclosed=wire["helpers_disclosed"])
    if form == "durable":
        owner = wire.get("owner")
        if keys != {"owner"} or owner not in {o.value for o in DurableOwner}:
            raise DescriptorError("durable takes exactly owner: host | environment")
        return Durable(owner=DurableOwner(owner))
    if form == "argv":
        required = {"executable", "observe_argv", "observe_ok_exit", "stop_argv", "timeout_s"}
        if not required <= keys or not keys <= required | {"remove_argv"}:
            raise DescriptorError(f"argv takes {sorted(required)} and optional remove_argv")
        executable = wire["executable"]
        exits = wire["observe_ok_exit"]
        timeout = wire["timeout_s"]
        if not isinstance(executable, str) or not executable:
            raise DescriptorError("argv executable must be a non-empty string")
        if not isinstance(exits, (list, tuple)) or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in exits
        ):
            raise DescriptorError("argv observe_ok_exit must be a list of integers")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise DescriptorError("argv timeout_s must be a positive number")
        remove = wire.get("remove_argv")
        return ArgvRelease(
            executable=executable,
            observe_argv=_strs(wire["observe_argv"], "observe_argv"),
            observe_ok_exit=frozenset(exits),
            stop_argv=_strs(wire["stop_argv"], "stop_argv"),
            timeout=timedelta(seconds=timeout),
            remove_argv=None if remove is None else _strs(remove, "remove_argv"),
        )
    raise DescriptorError(f"unknown release form {form!r}: one of {FORMS} is required")


def as_descriptor(release: object) -> ReleaseDescriptor:
    """What a port returned from `release_descriptor`: a form, or its wire mapping."""
    if isinstance(release, (InRunGroup, ArgvRelease, Durable)):
        return release
    if isinstance(release, Mapping):
        return descriptor_from_wire(release)
    raise DescriptorError(f"not a release descriptor: {type(release).__name__}")


# ==================== V-5 facet markers


@final
@dataclass(frozen=True, slots=True)
class EffectCall:
    """What `release_descriptor` is derived from (V-5): the call's own arguments by name, the
    ticket excluded, plus the effect's declared `lifetime` and `release_timeout`, copied
    unchanged by `Ticketed` (pass-through, never compared in core)."""

    member: str
    arguments: Mapping[str, object]
    lineage: Lineage
    effect: EffectId
    lifetime: Lifetime
    release_timeout: timedelta | None


class ReadFacet(Protocol):
    """Observes; never changes observed state (V-5.1)."""


class EffectFacet(Protocol):
    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor: ...  # pure (V-5.4)


class CreateFacet(EffectFacet, Protocol):
    """Every method takes an `AttemptTicket`; its confirmation yields a `CreatedHandle`."""


class OwnedEffectFacet(EffectFacet, Protocol):
    """Every method takes an `OwnedHandle` (or `CreatedHandle`) and an `AttemptTicket`."""


class SafeStartFacet(EffectFacet, Protocol):
    """Verbs limited to start | refresh | install; may take a `FoundRef`."""


class EventFacet(EffectFacet, Protocol):
    """Never yields a handle; processes it starts stay attributable to the run (V-2.3)."""


class HasRecordedResult(Protocol):
    @property
    def recorded(self) -> RecordedResult: ...  # V-5.5


# ==================== resource port


@final
@dataclass(frozen=True, slots=True)
class Resolved:
    executable: str  # absolute; inside an adopted home or the envelope's install directory
    reported_version: str  # from the resolved executable itself, never via mise exec (B3-C12)
    pin_fingerprint: str
    adoption_fingerprint: str  # joined against as a currency fact (V-9)


@final
@dataclass(frozen=True, slots=True)
class BoundCommand:
    task: CatalogEntry  # an allowlisted catalog argv entry; never `mise run`, `mise exec`, a shim
    argv: tuple[str, ...]  # argv[0] == resolved.executable
    environment: Mapping[str, str]  # built from empty plus an allowlist
    resolved: Resolved
    reports_tests: bool  # a test selector's result must carry counts (B3-C14)


@final
@dataclass(frozen=True, slots=True)
class ResourceSpec:
    logical_system: str
    realization: RealizationKind
    entry: CatalogEntry
    command: BoundCommand | None  # required iff realization is AGENT_LAUNCHED_PROJECT (B3-C4)


@final
@dataclass(frozen=True, slots=True)
class ResourceObservation:
    selector_present: bool  # only the instance this root's run-scoped selector names (V-10.1)
    selector_ref: SelectorRef | None  # set iff selector_present (V-4.6)
    identity_proven: bool  # never from port occupancy alone (WR-OWN-7)
    configuration_compatible: bool
    currency: tuple[CurrencyFact, ...]
    found: tuple[FoundRef, ...]  # <= FOUND_MAX (V-13); the adapter keeps the first FOUND_MAX
    code: StableCode | None  # set iff the port could not observe (V-3.8)


@final
@dataclass(frozen=True, slots=True)
class Endpoint:
    scheme: str
    host: str
    port: int


@final
@dataclass(frozen=True, slots=True)
class RouteRefused:
    code: StableCode
    human_action: HumanAction  # <= HUMAN_ACTION_MAX (V-13)


class SelfProvisioning(StrEnum):
    DISABLED_BY_CONFIGURATION = "disabled_by_configuration"
    BLOCKS = "blocks"


class Helpers(StrEnum):
    PREVENTED = "prevented"
    CONTAINED = "contained"  # every process it starts is attributable to the run (V-2.3)
    DISCLOSED = "disclosed"  # a helper may outlive the run (CG-1c)


@final
@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    self_provisioning: SelfProvisioning
    helpers: Helpers
    disclosure: BoundedText | None  # required iff helpers is DISCLOSED


class ResourceReads(ReadFacet, Protocol):
    def observe(
        self, spec: ResourceSpec, lineage: Lineage, effect: EffectId | None
    ) -> ResourceObservation: ...

    def check(
        self, check: CheckRef, target: CreatedHandle | OwnedHandle | FoundRef | SelectorRef
    ) -> CheckResult: ...

    def endpoint(
        self, target: CreatedHandle | OwnedHandle | FoundRef | SelectorRef, vantage: Vantage
    ) -> Endpoint | RouteRefused: ...


class ResourceCreate(CreateFacet, Protocol):
    def launch_policy(self, spec: ResourceSpec) -> ExecutionPolicy: ...  # pure (B3-C4)

    def create(self, spec: ResourceSpec, ticket: AttemptTicket) -> Confirmation: ...


class ResourceOwned(OwnedEffectFacet, Protocol):
    def restart(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation: ...

    def recreate(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation: ...

    def stop(self, target: CreatedHandle, ticket: AttemptTicket) -> Confirmation: ...


class ResourceSafeStart(SafeStartFacet, Protocol):
    def start(self, target: FoundRef | OwnedHandle, ticket: AttemptTicket) -> Confirmation: ...

    # stopped -> running only (B3-C6): never restart, recreate, reconfigure or delete (WR-OWN-10)


# ==================== grant port (demo credentials only)


@final
@dataclass(frozen=True, slots=True)
class GrantObservation:
    identity: str  # <= TOKEN_MAX (V-13)
    expires_at: Instant
    generation: str  # <= TOKEN_MAX (V-13)
    interactive_required: bool
    found: FoundRef  # the host demo credential; the only value GrantRefresh.refresh accepts
    code: StableCode | None  # GRANT_ISSUER_UNREACHABLE when the issuer cannot be read (B3-C8)
    # no field can hold a secret value (WR-EVID-12)


@final
@dataclass(frozen=True, slots=True)
class ConsumerCurrency:
    authenticated: bool  # one authenticated call made from inside the consumer (WR-VERIFY-6)
    generation_seen: str | None  # <= TOKEN_MAX (V-13)
    code: StableCode | None  # CREDENTIAL_STALE only when the port proved generation_seen older


def grant_currency(consumer: ConsumerCurrency, host: GrantObservation) -> CurrencyFact | None:
    """Fixed by the contract, not by an adapter (B3-C19): None iff the consumer did not
    authenticate, saw no generation, or the host observation carries a code; else
    `CurrencyFact(DEMO_CREDENTIAL, generation_seen, host.expires_at, older)` with `older` the
    stale code exactly when the port reported it."""
    if not consumer.authenticated or consumer.generation_seen is None or host.code is not None:
        return None
    return CurrencyFact(
        subject=HostScopeRef.DEMO_CREDENTIAL,
        observed_generation=consumer.generation_seen,
        valid_until=host.expires_at,
        older=codes.CREDENTIAL_STALE if consumer.code == codes.CREDENTIAL_STALE else None,
    )


class GrantReads(ReadFacet, Protocol):
    def observe_host(self) -> GrantObservation: ...

    def observe_in_consumer(
        self, consumer: CreatedHandle | OwnedHandle | FoundRef | SelectorRef
    ) -> ConsumerCurrency: ...


class GrantRefresh(SafeStartFacet, Protocol):
    def refresh(self, grant: FoundRef, ticket: AttemptTicket) -> Confirmation: ...


class GrantDelivery(OwnedEffectFacet, Protocol):
    def deliver(self, consumer: OwnedHandle, ticket: AttemptTicket) -> Confirmation: ...


# ==================== resolver ports (read-only)


def toolchain_currency(resolved: Resolved) -> CurrencyFact:
    """Fixed by the contract (B3-C19): a fingerprint is either current or STALE."""
    return CurrencyFact(
        subject=HostScopeRef.TOOLCHAIN_INSTALLS,
        observed_generation=resolved.adoption_fingerprint,
        valid_until=None,
        older=None,
    )


@final
@dataclass(frozen=True, slots=True)
class HostScopeUnreadable:
    """The subject's source could not be read; the subject has no reading (V-9.7, B3-C18)."""

    subject: HostScopeRef
    code: StableCode  # HOST_SCOPE_UNREADABLE


class HostScopeReads(ReadFacet, Protocol):
    def read(
        self, subject: HostScopeRef
    ) -> tuple[HostScopeRef, str, Instant] | HostScopeUnreadable: ...


@final
@dataclass(frozen=True, slots=True)
class Unresolved:
    code: StableCode  # TOOLCHAIN_MISSING | TOOLCHAIN_INTERFACE_DRIFT | ADOPTION_STALE
    tool: str  # <= NAME_MAX (V-13)
    pin: str  # <= TOKEN_MAX (V-13)
    human_action: HumanAction  # <= HUMAN_ACTION_MAX (V-13)


class ToolchainResolver(ReadFacet, Protocol):
    def resolve(self, project: CatalogEntry, tool: str) -> Resolved | Unresolved: ...


@final
@dataclass(frozen=True, slots=True)
class Closure:
    services: frozenset[str]  # <= IDSET_MAX members (V-13)
    edges: frozenset[tuple[str, str]]  # <= 4 x IDSET_MAX (V-13)
    definition_fingerprint: str  # <= TOKEN_MAX (V-13)


@final
@dataclass(frozen=True, slots=True)
class ClosureRefused:
    code: StableCode  # UNKNOWN_IDENTIFIER | COMPOSE_DEFINITION_INVALID | BOUND_EXCEEDED
    subject: BoundedText


class ComposeResolver(ReadFacet, Protocol):
    def closure(
        self, project: CatalogEntry, selected: frozenset[str]
    ) -> Closure | ClosureRefused: ...


class ToolchainProvisioning(SafeStartFacet, Protocol):  # durable, host-scoped; not a resolver
    def refresh_adoption(self, project: CatalogEntry, ticket: AttemptTicket) -> Confirmation: ...

    def install_pinned(
        self, project: CatalogEntry, tool: str, ticket: AttemptTicket
    ) -> Confirmation: ...


# ==================== execution port (events)


class ExecutionClass(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CONTRACT_VIOLATION = "contract_violation"
    INTERRUPTED = "interrupted"


@final
@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_status: int
    classification: ExecutionClass
    counts: TestCounts | None  # None with reports_tests true -> CONTRACT_VIOLATION (B3-C14)
    failing: tuple[str, ...]
    code: StableCode | None
    excerpt: BoundedText  # console output is evidence, never protocol

    @property
    def recorded(self) -> RecordedResult:
        """`RecordedResult(passed, code, counts)`; passed iff the classification is PASSED
        (V-5.5). Satisfies `HasRecordedResult`."""
        return RecordedResult(
            passed=self.classification is ExecutionClass.PASSED,
            code=self.code,
            counts=self.counts,
        )


class ExecutionPort(EventFacet, Protocol):
    def policy(self, command: BoundCommand) -> ExecutionPolicy: ...  # pure (B3-C14)

    def run(
        self,
        command: BoundCommand,
        ticket: AttemptTicket,
        cancel: CancelSignal,  # the only port member that takes cancel or a deadline (B3-C22)
        until: Instant,
    ) -> tuple[Confirmation, ExecutionResult | None]: ...  # None only with NOT_APPLIED (V-5.5)


# ==================== incidental write sets (V-5.1 (c))

INCIDENTAL_WRITES: Mapping[str, frozenset[str]] = {
    # read operation -> path patterns; ${ENVELOPE} and ${CONSUMER_EPHEMERAL} are declared roots.
    # A contract value (B3-C20), not an adapter setting: an implementation may not widen it.
    "ResourceReads.observe": frozenset(),
    "ResourceReads.check": frozenset(),
    "ResourceReads.endpoint": frozenset(),
    "ComposeResolver.closure": frozenset(),
    "GrantReads.observe_host": frozenset(),
    "GrantReads.observe_in_consumer": frozenset({"${CONSUMER_EPHEMERAL}/**"}),
    "ToolchainResolver.resolve": frozenset({"${ENVELOPE}/cache/**", "${ENVELOPE}/state/**"}),
    "HostScopeReads.read": frozenset({"${ENVELOPE}/cache/**", "${ENVELOPE}/state/**"}),
}

# ==================== port families (B3-C21)

FAMILIES: Mapping[str, tuple[type, ...]] = {
    # The one map from the seven adapter families to this boundary's protocols. A family is a
    # grouping of implementations, never a port of its own; there is no TestRun port (TestRun is
    # ExecutionPort) and no provisioning-submit port (a submit is ResourceCreate.create with
    # RealizationKind.PROVISIONED).
    "Command Execution": (ExecutionPort,),
    "Toolchain Resolution & Provisioning": (
        ToolchainResolver,
        ToolchainProvisioning,
        HostScopeReads,
    ),
    "Container Control": (ResourceReads, ResourceCreate, ResourceOwned, ResourceSafeStart),
    "Local Process Supervision": (ResourceReads, ResourceCreate, ResourceOwned),
    "Demo Credential Serving": (GrantReads, GrantRefresh, GrantDelivery, HostScopeReads),
    "Provisioning & Testing": (ResourceCreate, ResourceReads, ExecutionPort),
    "Read-Only Probes": (ResourceReads, ComposeResolver),
}
