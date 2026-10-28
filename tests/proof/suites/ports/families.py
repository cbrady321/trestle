"""The conformance cases of the port families that have implementations so far (B3-C17).

Importing this module registers each family in `core.REGISTRY`. A case reaches the implementation
only through the port's members, and everything implementation-specific comes from the
implementation factory's `Implementation.extras` (its fixture contract):

`Command Execution` (an `ExecutionPort`): `commands`, a mapping of the suite's five tasks --
`pass`, `fail`, `tests` (a test selector whose result carries counts), `tests_no_counts` (a test
selector whose result would carry none) and `long` (a command that runs until cancelled) -- to
bound commands.

`Local Process Supervision` (`ResourceReads` + `ResourceCreate` + `ResourceOwned` over a marker):
`spec` and, optionally, `provisioned_spec` (a `PROVISIONED` spec), `lifetimes` (the creation
lifetimes the implementation supports, `run` and/or `durable`), `plant_found(logical_system)` (make
an instance the root's selector does not name) and, in `Implementation.reach`, the engine inventory.

Reads that must be watched (V-5.1) are called under `read(built, operation)`; setup writes are not.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.proof.suites.ports import core
from trestle.common.plan import vocabulary
from trestle.workflow import ports
from trestle.workflow import services as svc
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat, Vantage
from trestle.workflow.values import (
    ConfirmationStatus,
    CreatedHandle,
    Lineage,
    NodePath,
    StopCause,
)

LINEAGE = Lineage("r_suite_0001", NodePath(("suite",)))
RELEASE_TIMEOUT = timedelta(seconds=30)
FUTURE = datetime(2999, 1, 1, tzinfo=UTC)
PAST = datetime(2000, 1, 1, tzinfo=UTC)

# The stable codes an interrupted run carries (B3-C14), from the vocabulary's own spelling.
EXECUTION_CANCELLED = vocabulary.EXECUTION_CANCELLED
EXECUTION_DEADLINE = vocabulary.EXECUTION_DEADLINE


class Cancel:
    """A `CancelSignal` the suite controls."""

    def __init__(self, cause: StopCause | None = None) -> None:
        self._cause = cause

    @property
    def requested(self) -> bool:
        return self._cause is not None

    def cause(self) -> StopCause | None:
        return self._cause

    def wait(self, timeout: timedelta) -> bool:
        return self.requested


@contextmanager
def read(built: core.Implementation, operation: str) -> Iterator[None]:
    """One read call under the V-5.1 watcher and the port's own reach (B3-C17 item 3)."""
    with core.watch(operation, built.reach):
        yield


def effect_call(
    member: str, arguments: dict[str, Any], lifetime: Lifetime = Lifetime.RUN, effect: str = "e"
) -> ports.EffectCall:
    return ports.EffectCall(member, arguments, LINEAGE, effect, lifetime, RELEASE_TIMEOUT)


def ticket(
    effect: str,
    facet: EffectFacetClass,
    lifetime: Lifetime = Lifetime.RUN,
    attempt: int = 1,
    release: object = None,
) -> svc.AttemptTicket:
    return svc.AttemptTicket(
        LINEAGE,
        effect,
        facet,
        attempt,
        Repeat.SAFE,
        lifetime,
        release if release is not None else ports.InRunGroup(),
        None,
    )


def status(confirmation: Any) -> ConfirmationStatus:
    return ConfirmationStatus(getattr(confirmation.status, "value", confirmation.status))


# -------------------- Command Execution


def _run(built: core.Implementation, task: str, cancel: Cancel, until: datetime) -> tuple[Any, Any]:
    command = built.extras["commands"][task]
    return built.impl.run(command, ticket("test", EffectFacetClass.EVENT), cancel, until)


def command_descriptor_is_in_run_group_matching_policy(built: core.Implementation) -> None:
    impl = built.impl
    for task, command in built.extras["commands"].items():
        call = effect_call(
            "run", {"command": command, "cancel": Cancel(), "until": FUTURE}, effect="test"
        )
        first = ports.as_descriptor(impl.release_descriptor(call))
        assert isinstance(first, ports.InRunGroup), task
        # helpers_disclosed equals policy(command).helpers is DISCLOSED (B3-C3, CG-1)
        assert first.helpers_disclosed == (impl.policy(command).helpers == ports.Helpers.DISCLOSED)
        # produced before the effect, and equal across attempts (V-10.1)
        assert ports.as_descriptor(impl.release_descriptor(call)) == first


def command_policy_is_pure_and_total(built: core.Implementation) -> None:
    for command in built.extras["commands"].values():
        policy = built.impl.policy(command)
        assert policy == built.impl.policy(command)
        assert ports.Helpers(policy.helpers) in set(ports.Helpers)
        assert ports.SelfProvisioning(policy.self_provisioning) in set(ports.SelfProvisioning)
        if ports.Helpers(policy.helpers) is ports.Helpers.DISCLOSED:
            assert policy.disclosure  # required iff DISCLOSED


def command_run_takes_a_ticket_and_only_run_takes_cancel(built: core.Implementation) -> None:
    parameters = inspect.signature(built.impl.run).parameters
    assert list(parameters) == ["command", "ticket", "cancel", "until"]
    policy_params = inspect.signature(built.impl.policy).parameters
    assert list(policy_params) == ["command"]  # a pure member: no ticket, no cancel


def command_pass_and_fail_are_recorded_as_such(built: core.Implementation) -> None:
    confirmation, result = _run(built, "pass", Cancel(), FUTURE)
    assert status(confirmation) is ConfirmationStatus.APPLIED
    assert ports.ExecutionClass(result.classification) is ports.ExecutionClass.PASSED
    assert result.recorded.passed is True
    confirmation, result = _run(built, "fail", Cancel(), FUTURE)
    assert status(confirmation) is ConfirmationStatus.APPLIED
    assert ports.ExecutionClass(result.classification) is ports.ExecutionClass.FAILED
    assert result.recorded.passed is False


def command_errors_are_counted(built: core.Implementation) -> None:
    _, result = _run(built, "tests", Cancel(), FUTURE)
    counts = result.counts
    assert counts is not None
    assert all(
        isinstance(n, int) for n in (counts.passed, counts.failed, counts.errors, counts.skipped)
    )
    assert result.recorded.counts is not None  # V-5.5: the projection carries the counts


def command_reports_tests_without_counts_is_contract_violation(built: core.Implementation) -> None:
    _, result = _run(built, "tests_no_counts", Cancel(), FUTURE)
    assert result is not None
    assert ports.ExecutionClass(result.classification) is ports.ExecutionClass.CONTRACT_VIOLATION
    assert result.recorded.passed is False


def command_cancel_and_until_yield_interrupted_with_code(built: core.Implementation) -> None:
    for cancel, until, code in (
        (Cancel(StopCause.CANCEL), FUTURE, EXECUTION_CANCELLED),
        (Cancel(StopCause.RELEASE_POINT), FUTURE, EXECUTION_DEADLINE),
        (Cancel(), PAST, EXECUTION_DEADLINE),
    ):
        confirmation, result = _run(built, "long", cancel, until)
        assert status(confirmation) is ConfirmationStatus.APPLIED  # it started, then was ended
        assert result is not None  # never None for a started process
        assert ports.ExecutionClass(result.classification) is ports.ExecutionClass.INTERRUPTED
        assert result.code == code
        assert result.recorded.passed is False


def command_none_result_only_with_not_applied(built: core.Implementation) -> None:
    for task in built.extras["commands"]:
        confirmation, result = _run(built, task, Cancel(), FUTURE)
        assert result is not None or status(confirmation) is ConfirmationStatus.NOT_APPLIED


COMMAND_CASES = (
    core.Case(
        "descriptor_in_run_group_matches_policy", command_descriptor_is_in_run_group_matching_policy
    ),
    core.Case("policy_pure_and_total", command_policy_is_pure_and_total),
    core.Case("run_signature", command_run_takes_a_ticket_and_only_run_takes_cancel),
    core.Case("pass_and_fail", command_pass_and_fail_are_recorded_as_such),
    core.Case("errors_counted", command_errors_are_counted),
    core.Case(
        "reports_tests_without_counts", command_reports_tests_without_counts_is_contract_violation
    ),
    core.Case("cancel_and_until_interrupted", command_cancel_and_until_yield_interrupted_with_code),
    core.Case("none_result_only_not_applied", command_none_result_only_with_not_applied),
)

# -------------------- Local Process Supervision


def _lifetimes(built: core.Implementation) -> list[Lifetime]:
    return [Lifetime(v) for v in built.extras["lifetimes"]]


def _create(
    built: core.Implementation,
    effect: str = "up",
    lifetime: Lifetime | None = None,
    attempt: int = 1,
) -> tuple[Any, svc.AttemptTicket]:
    life = lifetime or _lifetimes(built)[0]
    t = ticket(effect, EffectFacetClass.CREATE, life, attempt)
    return built.impl.create(built.extras["spec"], t), t


def _observe(built: core.Implementation, effect: str | None = "up") -> Any:
    with read(built, "ResourceReads.observe"):
        return built.impl.observe(built.extras["spec"], LINEAGE, effect)


def marker_absent_before_create(built: core.Implementation) -> None:
    seen = _observe(built)
    assert seen.selector_present is False and seen.selector_ref is None
    assert seen.code is None and tuple(seen.found) == ()
    assert _observe(built, None).selector_present is False  # a node with no CREATE effect


def marker_create_applies_and_is_observed(built: core.Implementation) -> None:
    for life in _lifetimes(built):
        effect = f"up-{life.value}"
        conf, _ = _create(built, effect, life)
        assert status(conf) is ConfirmationStatus.APPLIED and conf.identity
        seen = _observe(built, effect)
        assert seen.selector_present is True and seen.selector_ref is not None
        assert seen.selector_ref.selector == conf.identity  # the ref names the created instance
        assert seen.identity_proven is True
        # NOT_APPLIED is a claim of no change: after a change it is never returned
        assert status(conf) is not ConfirmationStatus.NOT_APPLIED


def marker_found_instance_only_in_found(built: core.Implementation) -> None:
    system = built.extras["spec"].logical_system
    planted = built.extras["plant_found"](system)
    seen = _observe(built)
    assert seen.selector_present is False and seen.selector_ref is None
    assert [f.selector for f in seen.found] == [planted]
    conf, _ = _create(built)
    seen = _observe(built)
    assert seen.selector_present is True and seen.selector_ref.selector == conf.identity
    assert [f.selector for f in seen.found] == [planted]  # the created one is not "found"


def marker_create_idempotent_per_selector(built: core.Implementation) -> None:
    before = built.reach.engine_inventory()["containers"]
    first, _ = _create(built, attempt=1)
    again, _ = _create(built, attempt=2)  # attempt 1 landed unconfirmed (B3-7)
    assert status(first) is ConfirmationStatus.APPLIED is status(again)
    assert first.identity == again.identity
    added = built.reach.engine_inventory()["containers"] - before
    assert added == {first.identity}  # one instance


def marker_descriptor_forms_by_lifetime(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    lifetimes = _lifetimes(built)
    # every CREATE member is called with DURABLE and, where it has a RUN form, with RUN
    durable_form = ports.as_descriptor(
        built.impl.release_descriptor(effect_call("create", {"spec": spec}, Lifetime.DURABLE))
    )
    assert isinstance(durable_form, ports.Durable)
    if Lifetime.RUN in lifetimes:
        run_form = ports.as_descriptor(
            built.impl.release_descriptor(effect_call("create", {"spec": spec}, Lifetime.RUN))
        )
        assert not isinstance(run_form, ports.Durable)
        assert isinstance(run_form, (ports.InRunGroup, ports.ArgvRelease))
        if isinstance(run_form, ports.ArgvRelease):
            assert run_form.timeout <= RELEASE_TIMEOUT  # ArgvRelease.timeout <= release_timeout
            assert run_form.remove_argv is not None
        else:
            policy = built.impl.launch_policy(spec)
            assert run_form.helpers_disclosed == (policy.helpers == ports.Helpers.DISCLOSED)


def marker_descriptor_equal_across_attempts_and_produced_before_the_effect(
    built: core.Implementation,
) -> None:
    spec = built.extras["spec"]
    before = built.reach.engine_inventory()
    for life in _lifetimes(built):
        call = effect_call("create", {"spec": spec}, life, effect="up")
        a = ports.as_descriptor(built.impl.release_descriptor(call))
        b = ports.as_descriptor(built.impl.release_descriptor(call))
        assert a == b
    assert built.reach.engine_inventory() == before  # deriving a descriptor changes nothing


def marker_provisioned_spec_is_durable_environment(built: core.Implementation) -> None:
    provisioned = built.extras.get("provisioned_spec")
    if provisioned is None:
        return
    form = ports.as_descriptor(
        built.impl.release_descriptor(effect_call("create", {"spec": provisioned}, Lifetime.RUN))
    )
    assert form == ports.Durable(ports.DurableOwner.ENVIRONMENT)  # B3-C3, assumed pending F-B3-2


def marker_owned_members_carry_the_handles_release(built: core.Implementation) -> None:
    conf, made = _create(built)
    descriptor = ports.as_descriptor(
        built.impl.release_descriptor(
            effect_call("create", {"spec": built.extras["spec"]}, made.lifetime, effect="up")
        )
    )
    handle = CreatedHandle(LINEAGE, "up", conf.identity, descriptor)
    for member in ("restart", "recreate", "stop"):
        call = effect_call(member, {"target": handle}, effect=f"{member}-effect")
        assert ports.as_descriptor(built.impl.release_descriptor(call)) == handle.release


def marker_repair_keeps_one_target(built: core.Implementation) -> None:
    conf, made = _create(built)
    handle = CreatedHandle(LINEAGE, "up", conf.identity, ports.InRunGroup())
    for member in ("restart", "recreate"):
        t = ticket(f"{member}-effect", EffectFacetClass.OWNED, made.lifetime)
        again = getattr(built.impl, member)(handle, t)
        assert status(again) is ConfirmationStatus.APPLIED and again.identity == conf.identity
        assert _observe(built).selector_present is True


def marker_stop_reads_absent(built: core.Implementation) -> None:
    conf, made = _create(built)
    handle = CreatedHandle(LINEAGE, "up", conf.identity, ports.InRunGroup())
    stopped = built.impl.stop(handle, ticket("stop-effect", EffectFacetClass.OWNED, made.lifetime))
    assert status(stopped) is ConfirmationStatus.APPLIED
    assert _observe(built).selector_present is False  # released means observed absent (V-3.8)


def marker_check_and_endpoint_reach_only_the_named_instance(built: core.Implementation) -> None:
    one, made = _create(built, "one")
    two, _ = _create(built, "two")
    ref_one = _observe(built, "one").selector_ref
    ref_two = _observe(built, "two").selector_ref
    assert ref_one.selector == one.identity and ref_two.selector == two.identity
    assert ref_one.selector != ref_two.selector
    handle_two = CreatedHandle(LINEAGE, "two", two.identity, ports.InRunGroup())
    built.impl.stop(handle_two, ticket("stop-two", EffectFacetClass.OWNED, made.lifetime))
    with read(built, "ResourceReads.check"):
        first = built.impl.check("ready", ref_one)
    with read(built, "ResourceReads.check"):
        second = built.impl.check("ready", ref_two)
    assert first.satisfied is True  # the instance the ref names...
    assert second.satisfied is False  # ...and no other: the stopped one does not answer for it
    with read(built, "ResourceReads.endpoint"):
        endpoint = built.impl.endpoint(ref_one, Vantage.HOST)
    assert isinstance(endpoint, ports.Endpoint) or hasattr(endpoint, "port")


def marker_reads_leave_no_trace(built: core.Implementation) -> None:
    conf, _ = _create(built)
    ref = _observe(built).selector_ref
    for _ in range(3):  # repeated reads change nothing in the reach the watcher looks at
        _observe(built)
        with read(built, "ResourceReads.check"):
            built.impl.check("ready", ref)
        with read(built, "ResourceReads.endpoint"):
            built.impl.endpoint(ref, Vantage.HOST)
    assert conf.identity in built.reach.engine_inventory()["containers"]


MARKER_CASES = (
    core.Case("absent_before_create", marker_absent_before_create),
    core.Case("create_applies_and_is_observed", marker_create_applies_and_is_observed),
    core.Case("found_instance_only_in_found", marker_found_instance_only_in_found),
    core.Case("create_idempotent_per_selector", marker_create_idempotent_per_selector),
    core.Case("descriptor_forms_by_lifetime", marker_descriptor_forms_by_lifetime),
    core.Case(
        "descriptor_equal_and_before_the_effect",
        marker_descriptor_equal_across_attempts_and_produced_before_the_effect,
    ),
    core.Case(
        "provisioned_spec_durable_environment", marker_provisioned_spec_is_durable_environment
    ),
    core.Case("owned_members_carry_handle_release", marker_owned_members_carry_the_handles_release),
    core.Case("repair_keeps_one_target", marker_repair_keeps_one_target),
    core.Case("stop_reads_absent", marker_stop_reads_absent),
    core.Case(
        "check_endpoint_reach_named_instance",
        marker_check_and_endpoint_reach_only_the_named_instance,
    ),
    core.Case("reads_leave_no_trace", marker_reads_leave_no_trace),
)

COMMAND_EXECUTION = "Command Execution"
LOCAL_PROCESS_SUPERVISION = "Local Process Supervision"

core.register_family(COMMAND_EXECUTION, COMMAND_CASES)
core.register_family(LOCAL_PROCESS_SUPERVISION, MARKER_CASES)
