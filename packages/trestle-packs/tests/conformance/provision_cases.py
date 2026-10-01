"""The provisioning family's conformance cases (L.RB-6.1; B3-C1, B3-C3, B3-C4, B3-C17, B3-C21,
WR-ENV-4:authoritative-probe-read-only).

A provisioning submit is `ResourceCreate.create` with `PROVISIONED` and its probe is
`ResourceReads.observe`, the authoritative read (B3-C21). Registered through the suite core's
`register_family` as family `provision` and run, UNMODIFIED, by `run_family` against the stdlib
fake and the real adapter (WR-PROOF-4). A case reaches the implementation only through the port's
members and the factory's `Implementation.extras`, its fixture contract:

- `spec`: a `PROVISIONED` `ResourceSpec` whose `logical_system` names the system a record belongs
  to; `secret`: the fixture password the store demands, which no result may carry;
- `plant_found(system)`: put a record of `system` in the store under a key this root's selector
  does not name, and return that key;
- `unreadable()`: an `Implementation` bound to a store that does not answer (a wrong password, a
  store that is down): every read is V-3.8's could-not-observe shape and a submit changes nothing;
- the implementation's `reach.engine_inventory`: `records` (the keys in the store) and whatever
  else the engine holds, which the read-facet watcher compares around every read (B3-C17 (3)).

No case branches on which implementation it runs against (`test_cases_unbranched`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from tests.proof.suites.ports import core
from tests.proof.suites.ports.families import LINEAGE, effect_call, read, status, ticket
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Vantage
from trestle.workflow.values import ConfirmationStatus

FAMILY = "provision"
ROUTE_UNSUPPORTED = "admission.route_unsupported"
SELECTOR = f"trwr-{LINEAGE.root_run_id}-{'.'.join(LINEAGE.path.segments)}"  # MC-B-01, written out


def _create(built: core.Implementation, effect: str = "up", attempt: int = 1) -> Any:
    made = ticket(effect, EffectFacetClass.CREATE, Lifetime.DURABLE, attempt)
    return built.impl.create(built.extras["spec"], made)


def _observe(built: core.Implementation, effect: str | None = "up") -> Any:
    with read(built, "ResourceReads.observe"):
        return built.impl.observe(built.extras["spec"], LINEAGE, effect)


def _records(built: core.Implementation) -> frozenset[str]:
    assert built.reach.engine_inventory is not None
    return frozenset(built.reach.engine_inventory()["records"])


def _refused(action: Callable[[], object]) -> bool:
    try:
        action()
    except ValueError:
        return True
    return False


def absent_before_submit(built: core.Implementation) -> None:
    seen = _observe(built)
    assert seen.selector_present is False and seen.selector_ref is None
    assert seen.code is None and tuple(seen.found) == ()  # observed absent, not could-not-observe
    assert _observe(built, None).selector_present is False  # a node with no CREATE effect


def submit_applies_and_is_observed(built: core.Implementation) -> None:
    conf = _create(built)
    assert status(conf) is ConfirmationStatus.APPLIED
    assert conf.identity == SELECTOR  # the record's key is the run-scoped selector (MC-B-01)
    seen = _observe(built)
    assert seen.selector_present is True and seen.selector_ref is not None
    assert seen.selector_ref.selector == conf.identity
    assert seen.identity_proven is True and seen.code is None
    assert _records(built) == {conf.identity}


def found_record_only_in_found(built: core.Implementation) -> None:
    planted = built.extras["plant_found"](built.extras["spec"].logical_system)
    seen = _observe(built)
    assert seen.selector_present is False and seen.selector_ref is None
    assert [f.selector for f in seen.found] == [planted]
    conf = _create(built)
    seen = _observe(built)
    assert seen.selector_present is True and seen.selector_ref.selector == conf.identity
    assert [f.selector for f in seen.found] == [planted]  # the submitted one is not "found"
    assert _records(built) == {planted, conf.identity}  # the found record was never touched


def submit_is_idempotent_per_selector(built: core.Implementation) -> None:
    before = _records(built)
    first = _create(built, attempt=1)
    again = _create(built, attempt=2)  # attempt 1 landed unconfirmed (B3-7)
    assert status(first) is ConfirmationStatus.APPLIED is status(again)
    assert first.identity == again.identity
    assert _records(built) - before == {first.identity}  # one record


def durable_descriptor_is_environment(built: core.Implementation) -> None:
    call = effect_call("create", {"spec": built.extras["spec"]}, Lifetime.DURABLE)
    form = ports.as_descriptor(built.impl.release_descriptor(call))
    assert form == ports.Durable(ports.DurableOwner.ENVIRONMENT)  # B3-C3, assumed pending F-B3-2


def run_lifetime_has_no_form_and_is_refused(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    before = _records(built)
    # a RUN call has no RUN form: the adapter refuses it (V-10.2), never a RUN-shaped descriptor
    run_call = effect_call("create", {"spec": spec}, Lifetime.RUN)
    assert _refused(lambda: built.impl.release_descriptor(run_call))
    run_ticket = ticket("up", EffectFacetClass.CREATE, Lifetime.RUN)
    assert _refused(lambda: built.impl.create(spec, run_ticket))  # before any machine call
    assert _records(built) == before  # nothing was submitted


def descriptor_is_equal_across_calls_and_produced_before_the_effect(
    built: core.Implementation,
) -> None:
    before = _records(built)
    call = effect_call("create", {"spec": built.extras["spec"]}, Lifetime.DURABLE)
    first = ports.as_descriptor(built.impl.release_descriptor(call))
    assert ports.as_descriptor(built.impl.release_descriptor(call)) == first  # V-10.1
    assert _records(built) == before  # deriving a descriptor changes nothing


def authoritative_probe_sees_the_submit_and_the_convenience_read_is_never_ahead(
    built: core.Implementation,
) -> None:
    conf = _create(built)
    ref = _observe(built).selector_ref
    assert ref is not None and ref.selector == conf.identity  # the probe is authoritative at once
    for _ in range(6):
        with read(built, "ResourceReads.check"):
            satisfied = built.impl.check("recorded", ref).satisfied
        # a convenience read may lag the submit, but is never ahead of the authoritative probe
        assert (not satisfied) or _observe(built).selector_present is True
        assert _observe(built).selector_present is True  # completion is read from the probe only


def an_unreadable_store_is_could_not_observe_never_absent(built: core.Implementation) -> None:
    down = built.extras["unreadable"]()
    seen = down.impl.observe(built.extras["spec"], LINEAGE, "up")
    assert seen.code is not None  # V-3.8: could not observe
    assert seen.selector_present is False and seen.selector_ref is None
    assert tuple(seen.found) == ()
    conf = _create(built)
    ref = _observe(built).selector_ref
    assert status(conf) is ConfirmationStatus.APPLIED and ref is not None
    checked = down.impl.check("recorded", ref)
    assert checked.satisfied is False and checked.code is not None
    refused = down.impl.create(
        built.extras["spec"], ticket("down", EffectFacetClass.CREATE, Lifetime.DURABLE)
    )
    assert status(refused) is ConfirmationStatus.NOT_APPLIED  # nothing changed, and it says so
    assert refused.code is not None


def reads_leave_no_trace(built: core.Implementation) -> None:
    _create(built)
    ref = _observe(built).selector_ref
    before = dict(built.reach.engine_inventory())
    for _ in range(3):  # repeated reads change nothing in the reach the watcher looks at
        _observe(built)
        with read(built, "ResourceReads.check"):
            built.impl.check("recorded", ref)
        with read(built, "ResourceReads.endpoint"):
            built.impl.endpoint(ref, Vantage.HOST)
    assert dict(built.reach.engine_inventory()) == before


def a_provisioned_record_has_no_route(built: core.Implementation) -> None:
    _create(built)
    ref = _observe(built).selector_ref
    with read(built, "ResourceReads.endpoint"):
        refused = built.impl.endpoint(ref, Vantage.HOST)
    assert refused.code == ROUTE_UNSUPPORTED and refused.human_action
    assert not hasattr(refused, "port")


def no_result_carries_the_secret(built: core.Implementation) -> None:
    secret = built.extras["secret"]
    conf = _create(built)
    seen = _observe(built)
    checked = built.impl.check("recorded", seen.selector_ref)
    down = built.extras["unreadable"]()
    failed_read = down.impl.observe(built.extras["spec"], LINEAGE, "up")
    failed_check = down.impl.check("recorded", seen.selector_ref)
    everything = repr((conf, seen, checked, failed_read, failed_check))
    assert secret not in everything and secret != ""


CASES: Sequence[core.Case] = (
    core.Case("absent_before_submit", absent_before_submit),
    core.Case("submit_applies_and_is_observed", submit_applies_and_is_observed),
    core.Case("found_record_only_in_found", found_record_only_in_found),
    core.Case("submit_is_idempotent_per_selector", submit_is_idempotent_per_selector),
    core.Case("durable_descriptor_is_environment", durable_descriptor_is_environment),
    core.Case("run_lifetime_has_no_form_and_is_refused", run_lifetime_has_no_form_and_is_refused),
    core.Case(
        "descriptor_equal_across_calls_and_before_the_effect",
        descriptor_is_equal_across_calls_and_produced_before_the_effect,
    ),
    core.Case(
        "authoritative_probe_and_convenience_read",
        authoritative_probe_sees_the_submit_and_the_convenience_read_is_never_ahead,
    ),
    core.Case(
        "unreadable_store_is_could_not_observe_never_absent",
        an_unreadable_store_is_could_not_observe_never_absent,
    ),
    core.Case("reads_leave_no_trace", reads_leave_no_trace),
    core.Case("a_provisioned_record_has_no_route", a_provisioned_record_has_no_route),
    core.Case("no_result_carries_the_secret", no_result_carries_the_secret),
)

core.register_family(FAMILY, CASES)
