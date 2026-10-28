"""One inducer per node-local condition, at depth two (L.TR-4.6; MC-B3-01 behaviour fixture;
consumed by L.TR-L.10).

`app` runs `stage`, a group whose one leaf `probe` sits two levels below the root (path
`stage/probe`). `CONDITION` is one source line (a published declaration is one value): the leaf ends
in exactly the condition it names, and the same leaf, alone as a root, ends in the same condition
at depth zero, so a test compares the two node classes (B4-T2). The six conditions:

- `pass`: the leaf creates its marker and it is ready (`passed`).
- `assertion`: a RECORDED `test` event comes back failed with counts (J-6; `failed`).
- `mfa`: the demo-credential refresh is NOT_APPLIED(`CREDENTIAL_INTERACTIVE`) on a stub grant port
  (B3-C11: no real issuer exists), joined by J-5a (`blocked`).
- `never_ready`: `FakeMarker(never_ready=True)`, no declared remedy, a short wait: the wait runs out
  (`POSTCONDITION_TIMEOUT`, J-20/J-23a; class `EXHAUSTED`, outcome `BLOCKED` under OQ-32).
- `no_progress`: the fault persists after the declared repair (J-3a `REMEDY_NO_PROGRESS`;
  `blocked`, never `passed` and never `repaired`).
- `exception`: `advance` raises (`UNIT_RAISED`; `EXECUTION_ERROR`).

`Probe(condition)` is the unit and `make_ports(condition, root)` the fake ports it runs on, so an
in-library test builds the same leaf at either depth. Three vertices, depth 3."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trestle_packs.fakes import (
    BoundCommand,
    Confirmation,
    ConfirmationStatus,
    FakeCommand,
    FakeMarker,
    Resolved,
    TestCounts,
    durable,
    failed_result,
)

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import (
    ExecutionPort,
    GrantObservation,
    GrantReads,
    GrantRefresh,
    ResourceCreate,
    ResourceOwned,
    ResourceReads,
    ResourceSpec,
)
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import CheckResult, CreatedHandle, FoundRef, Observation, Verdict

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references; `depth` is
# the longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 3, "depth": 3, "shared": None, "expect": "valid"}

CONDITIONS = ("pass", "assertion", "mfa", "never_ready", "no_progress", "exception")
CONDITION = "pass"

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
RESTART_EFFECT = "restart"
TEST_EFFECT = "test"
REFRESH_EFFECT = "refresh"
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "depth2-marker", None)
HOT = "marker.hot"  # the code the unready marker reports (the remedy's trigger)
FAILED_CODE = "test.assertion_failed"
COUNTS = TestCounts(passed=3, failed=1, errors=0, skipped=0)
SUBJECT = "demo-user@example.test"  # the identity the refresh needs an interactive login for
COMMAND = BoundCommand("test", ("/bin/true",), {}, Resolved("/bin/true", "1", "p", "a"), True)
ISSUER = "demo-issuer"  # the FoundRef.selector of the host demo credential

LEAF_BUDGET_S = 30
STAGE_BUDGET_S = 45
APP_BUDGET_S = 60
DEADLINE_S = 120


def _effect(
    effect: str,
    facet: EffectFacetClass,
    *,
    lifetime: Lifetime = Lifetime.RUN,
    release_timeout_s: float | None = None,
    release: bool = False,
    verb: str = "",
) -> EffectDeclaration:
    return EffectDeclaration(
        effect=effect,
        facet=facet,
        verb=verb,
        lifetime=lifetime,
        host_sections=frozenset(),
        release_timeout=None if release_timeout_s is None else timedelta(seconds=release_timeout_s),
        is_release=release,
    )


def declaration(condition: str) -> LeafDeclaration:
    """The leaf's declaration for `condition`. Every wait is short (the wait running out is the
    `never_ready` case); the budget covers wait + remedy + release."""
    completion = (
        CompletionSource.RECORDED if condition == "assertion" else CompletionSource.OBSERVED
    )
    effects: tuple[EffectDeclaration, ...]
    remedies: tuple[RemedyDeclaration, ...] = ()
    if condition == "assertion":
        effects = (_effect(TEST_EFFECT, EffectFacetClass.EVENT),)
    elif condition == "mfa":
        effects = (
            _effect(
                REFRESH_EFFECT,
                EffectFacetClass.SAFE_START,
                lifetime=Lifetime.DURABLE,
                verb="refresh",
            ),
        )
    else:
        effects = (
            _effect(CREATE_EFFECT, EffectFacetClass.CREATE, release_timeout_s=2),
            _effect(STOP_EFFECT, EffectFacetClass.OWNED, release_timeout_s=2, release=True),
        )
    if condition == "no_progress":
        effects += (_effect(RESTART_EFFECT, EffectFacetClass.OWNED),)
        remedies = (
            RemedyDeclaration(
                code=HOT,
                effect=RESTART_EFFECT,
                attempts=2,
                total=timedelta(seconds=6),
                cooldown=timedelta(seconds=0.5),
            ),
        )
    return LeafDeclaration(
        unit="probe",
        flags=LoopFlags(Compose.LEAF, completion, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=1.0)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=effects,
        retryable=frozenset(),
        remedies=remedies,
        budget=timedelta(seconds=LEAF_BUDGET_S),
        max_attempts=3,
    )


class StubGrant:
    """The demo-credential port as a STUB (B3-C11): the issuer needs an interactive login, so the
    refresh is NOT_APPLIED(`CREDENTIAL_INTERACTIVE`) carrying the identity that needs it."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self.refreshes = 0
        self._now = now or (lambda: datetime.now(UTC))

    def observe_host(self) -> GrantObservation:
        now = self._now()
        return GrantObservation(
            SUBJECT,
            now + timedelta(hours=1),
            "gen-1",
            True,
            FoundRef("credential", ISSUER, now),
            None,
        )

    def release_descriptor(self, call: Any) -> dict[str, Any]:
        return durable("host")  # a refresh is a durable, host-scoped effect (B3-C3)

    def refresh(self, grant: FoundRef, ticket: Any) -> Confirmation:
        self.refreshes += 1
        return Confirmation(
            ConfirmationStatus.NOT_APPLIED, "execution.credential_interactive", SUBJECT
        )


class ConditionsMarker(FakeMarker):
    """The marker of `never_ready` and `no_progress`: unready, reporting `HOT`; a confirmed repair
    changes nothing (`fixed_fingerprint`), so the trigger code persists after it."""

    def __init__(self, root: Any, *, fixed: bool) -> None:
        super().__init__(
            root, "run", never_ready=True, fixed_fingerprint=fixed, outcome_codes=(HOT,)
        )


class Probe:
    """Observe, act as `condition` says, release what it created."""

    def __init__(self, condition: str) -> None:
        self.condition = condition
        self.decl = declaration(condition)

    def declare(self) -> LeafDeclaration:
        return self.decl

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        if self.condition in ("assertion", "mfa"):
            return _absent()
        resource = reads.read(ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, CREATE_EFFECT)
        target = seen.selector_ref if seen.selector_ref is not None else (seen.found or (None,))[0]
        checked = resource.check("ready", target) if target is not None else None
        satisfied = checked is not None and checked.satisfied
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(
                satisfied,
                None if checked is None else checked.code,
                "" if checked is None else checked.detail,
            ),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            # J-20 / J-23 read this code on a present observation (V-3.1): the check's own
            code=seen.code
            if seen.code is not None
            else (None if checked is None else checked.code),
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if self.condition == "exception":
            raise RuntimeError("the probe raised")
        if self.condition == "assertion":
            until = ctx.clock.release_point
            effects.event(ExecutionPort).run(COMMAND, TEST_EFFECT, ctx.cancellation, until)
        elif self.condition == "mfa":
            grant = effects.read(GrantReads).observe_host()
            effects.safe_start(GrantRefresh).refresh(grant.found, REFRESH_EFFECT)
        elif state.remedy is not None and state.owned:
            effects.owned(ResourceOwned).restart(state.owned[-1], state.remedy.effect)
        else:
            effects.create(ResourceCreate).create(SPEC, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def _absent() -> Observation:
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


def make_ports(
    condition: str, root: Path, now: Callable[[], datetime] | None = None
) -> dict[type, object]:
    """The fake ports `condition` runs on; `root` is the directory the marker keeps its state in.
    `now` is the clock the time-reading fakes compare against (default: the wall clock, as in a real
    run): an in-library rig passes its manual clock, since the `until` the loop hands the command
    port is read off that clock, and against the wall clock it would expire once the wall clock
    passed the rig's fixed start (a date bomb, not a timing)."""
    if condition == "assertion":
        return {ExecutionPort: FakeCommand({"test": failed_result(FAILED_CODE, COUNTS)}, now=now)}
    if condition == "mfa":
        grant = StubGrant(now)
        return {GrantReads: grant, GrantRefresh: grant}
    if condition in ("never_ready", "no_progress"):
        marker: FakeMarker = ConditionsMarker(root / "markers", fixed=condition == "no_progress")
    else:
        marker = FakeMarker(root / "markers", "run")
    return {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}


ENTRY = WorkflowEntry(
    root="app",
    units={
        "app": AllDeclaration(
            unit="app",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(ChildBinding(unit="stage", params={}, needs=()),),
            concurrency=1,
            budget=timedelta(seconds=APP_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "stage": AllDeclaration(
            unit="stage",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(ChildBinding(unit="probe", params={}, needs=()),),
            concurrency=1,
            budget=timedelta(seconds=STAGE_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field=None,
        ),
        "probe": Probe(CONDITION),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=120, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def depth2_conditions(ctx: Context, env: str = "dev") -> dict[str, str]:
    run_tree(ctx, ENTRY, {"env": env}, ports=make_ports(CONDITION, ctx.tmp))
    return {"env": env, "condition": CONDITION}
