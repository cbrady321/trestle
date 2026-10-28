"""The four workflow-result conditions as one-vertex workflows on the fakes (L.SL-9.1; B4-T2,
B4-T3): a published workflow plugin whose one leaf ends in exactly the condition `CONDITION` names.

- `assertion_failure`: a RECORDED leaf whose `test` event is run through `FakeCommand` and comes
  back failed with counts (J-6: the recorded result is not passed, code `test.assertion_failed`).
- `mfa_needed`: the demo-credential refresh is NOT_APPLIED(`CREDENTIAL_INTERACTIVE`) with the
  identity that needs it; the port is a stub (`StubGrant`, B3-C11: no real issuer exists), joined by
  J-5a.
- `never_ready`: `FakeMarker(never_ready=True)`, no declared remedy, a short wait: the wait runs
  out (`POSTCONDITION_TIMEOUT`, J-20/J-23a).
- `no_progress`: the fault persists after the declared repair (`FakeMarker(fixed_fingerprint=True)`
  keeps the trigger code after a confirmed restart): J-3a `REMEDY_NO_PROGRESS`.

`CONDITION` is one source line (a published declaration is one value): a test publishes this file
with the line rewritten, once per condition (`tests/single/answer/test_ten_conditions_workflow.py`).
The unit is one class that branches on its condition, so the four cases share their observe and
release; each is answered by the host in one `run(completion="terminal")` call and read from the
answer alone. The plugin is self-contained (a published plugin may import only `trestle.plugin`,
`trestle.workflow` and the packs' fakes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from trestle.workflow.values import (
    CheckResult as UnitCheckResult,
)
from trestle.workflow.values import (
    CreatedHandle,
    FoundRef,
    Observation,
    Verdict,
)

CONDITIONS = ("assertion_failure", "mfa_needed", "never_ready", "no_progress")
CONDITION = "assertion_failure"
UNIT = "conditions_leaf"
CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
RESTART_EFFECT = "restart"
TEST_EFFECT = "test"
REFRESH_EFFECT = "refresh"
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)
HOT = "marker.hot"  # the code the unready marker reports (the remedy's trigger)
FAILED_CODE = "test.assertion_failed"
COUNTS = TestCounts(passed=3, failed=1, errors=0, skipped=0)
SUBJECT = "demo-user@example.test"  # the identity the refresh needs an interactive login for
COMMAND = BoundCommand("test", ("/bin/true",), {}, Resolved("/bin/true", "1", "p", "a"), True)
ISSUER = "demo-issuer"  # the FoundRef.selector of the host demo credential


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


def declaration(condition: str, *, env_key_field: str | None = "env") -> LeafDeclaration:
    """The leaf's declaration for `condition`. Every wait is short (the wait running out is the
    `never_ready` case); the budget covers wait + remedy + release."""
    completion = (
        CompletionSource.RECORDED if condition == "assertion_failure" else CompletionSource.OBSERVED
    )
    effects: tuple[EffectDeclaration, ...]
    remedies: tuple[RemedyDeclaration, ...] = ()
    if condition == "assertion_failure":
        effects = (_effect(TEST_EFFECT, EffectFacetClass.EVENT),)
    elif condition == "mfa_needed":
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
        unit=UNIT,
        flags=LoopFlags(Compose.LEAF, completion, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=1.0)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=effects,
        retryable=frozenset(),
        remedies=remedies,
        budget=timedelta(seconds=45),
        max_attempts=3,
        env_key_field=env_key_field,
    )


class StubGrant:
    """The demo-credential port as a STUB (B3-C11): the issuer needs an interactive login, so the
    refresh is NOT_APPLIED(`CREDENTIAL_INTERACTIVE`) carrying the identity that needs it. It reads
    and refreshes nothing; `refreshes` counts the calls that reached it."""

    def __init__(self) -> None:
        self.refreshes = 0

    def observe_host(self) -> GrantObservation:
        now = datetime.now(UTC)
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


class ConditionsLeaf:
    """Observe, act as `condition` says, release what it created; every call is counted."""

    def __init__(self, condition: str, decl: LeafDeclaration | None = None) -> None:
        self.condition = condition
        self.decl = decl or declaration(condition)
        self.observes = 0
        self.advances = 0
        self.releases = 0

    def declare(self) -> LeafDeclaration:
        return self.decl

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        self.observes += 1
        if self.condition in ("assertion_failure", "mfa_needed"):
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
            postcondition=UnitCheckResult(
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
        self.advances += 1
        if self.condition == "assertion_failure":
            until = ctx.clock.release_point
            effects.event(ExecutionPort).run(COMMAND, TEST_EFFECT, ctx.cancellation, until)
        elif self.condition == "mfa_needed":
            grant = effects.read(GrantReads).observe_host()
            effects.safe_start(GrantRefresh).refresh(grant.found, REFRESH_EFFECT)
        elif state.remedy is not None and state.owned:
            effects.owned(ResourceOwned).restart(state.owned[-1], state.remedy.effect)
        else:
            effects.create(ResourceCreate).create(SPEC, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        self.releases += 1
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def _absent() -> Observation:
    return Observation(
        present=False,
        selector_present=False,
        identity_proven=False,
        configuration_compatible=True,
        postcondition=UnitCheckResult(False, None, ""),
        preconditions=(),
        currency=(),
        found=(),
        code=None,
        payload=None,
    )


def make_unit(condition: str, decl: LeafDeclaration | None = None) -> ConditionsLeaf:
    return ConditionsLeaf(condition, decl)


ENTRY = WorkflowEntry(
    root=UNIT, units={UNIT: ConditionsLeaf(CONDITION)}, deadline=timedelta(seconds=120)
)


def _ports(ctx: Context, condition: str) -> dict[type, object]:
    if condition == "assertion_failure":
        return {ExecutionPort: FakeCommand({"test": failed_result(FAILED_CODE, COUNTS)})}
    if condition == "mfa_needed":
        grant = StubGrant()
        return {GrantReads: grant, GrantRefresh: grant}
    marker = ConditionsMarker(ctx.tmp / "markers", fixed=condition == "no_progress")
    return {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}


@trestle(deadline=120, env_arg="env")
def conditions_leaf(ctx: Context, env: str = "dev") -> dict[str, str]:
    run_tree(ctx, ENTRY, {"env": env}, ports=_ports(ctx, CONDITION))
    return {"env": env, "condition": CONDITION}
