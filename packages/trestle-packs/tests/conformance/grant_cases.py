"""The Demo Credential Serving family's cases: reads and refresh (L.RB-9.1; B3-C8, B3-C9, B3-C11,
B3-C17, B3-C19, B3-E1, B3-E4, WR-EVID-12, MC-B-06) and delivery (L.RB-9.2; B3-C11, B3-C3,
WR-ENV-13).

Registered through the suite core's `register_family` (L.SV-5.15) as families `grant` (reads and
refresh) and `grant_delivery` (delivery through a mounted refreshable file) and run,
UNMODIFIED, by `run_family` against every implementation: the stdlib `FakeGrant` and the demo
adapter over the stub issuer (WR-PROOF-4). A case reaches the implementation only through the
port's members and the implementation factory's `Implementation.extras`, its fixture contract:

- `identity`: the demo identity's name;
- `current_generation()`, `expires_at()`: what the issuer's `GrantObservation` must report;
- `advance() -> str`: the issuer issues a new generation behind the port's back (a host rotation);
- `set_interactive(bool)`, `set_reachable(bool)`: the identity needs interactive sign-in; the
  issuer stops (and again starts) answering;
- `plant_consumer(selector, generation=None)`: a consumer addressed by `selector` whose credential
  carries `generation` (default: the current one; a name the issuer never issued is allowed);
- `secret_values() -> tuple[str, ...]`: every credential-shaped string in the world, which no
  value the port returns may contain (WR-EVID-12);
- the implementation's `reach.issuer_generation` and `reach.consumer_ephemeral`, which the read
  watcher compares around every read (B3-C17 (3)).

`grant_delivery` runs `GrantDelivery` (`built.impl`) and asks its fixture contract for:

- `reads`: a `GrantReads` over the same world (the consumer-side authenticated call);
- `own_consumer(name, generation=None) -> OwnedHandle`: an owned consumer whose credential channel
  (a mounted refreshable file) holds `generation` (default: the current one); its release is
  whatever the implementation's consumer has; `unprovisioned_consumer(name) -> OwnedHandle`: an
  owned handle whose channel was never made;
- `channel_generation(handle) -> str | None`: the generation the consumer reads from its channel;
- `consumer_incarnation(handle)`: changes only if the consumer is recreated or restarted (a
  container's id and start time; a process's pid), and `consumer_environment(handle)`: what the
  consumer was created with;
- `advance()`, `current_generation()`, `set_reachable(bool)`, `secret_values()` as above.

Generations are opaque names whose string order is NOT the order the issuer issued them in, so
only the issuer's own ordering can say "older" (B3-C9); the stale case makes a later generation
sort below the first. No case branches on which implementation it runs
against (`test_cases_unbranched`).
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from tests.proof.suites.ports import core
from tests.proof.suites.ports.families import (
    LINEAGE,
    RELEASE_TIMEOUT,
    read,
    status,
    ticket,
)
from trestle.common.plan import bounds
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime
from trestle.workflow.values import (
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    SelectorRef,
)

FAMILY = "grant"
DELIVERY_FAMILY = "grant_delivery"
CREDENTIAL_NAME = re.compile(r"(?i)token|secret|credential|passw|api_?key|auth")
GRANT_ISSUER_UNREACHABLE = "adapter.grant_issuer_unreachable"
CREDENTIAL_INTERACTIVE = "execution.credential_interactive"
CREDENTIAL_STALE = "execution.credential_stale"
DEMO_CREDENTIAL_KIND = "demo_credential"
NEVER_ISSUED = "gen-never-issued"


def refresh_ticket(attempt: int = 1) -> Any:
    return ticket(
        "refresh",
        EffectFacetClass.SAFE_START,
        Lifetime.DURABLE,
        attempt,
        release=ports.Durable(ports.DurableOwner.HOST),
    )


def effect_call(member: str, arguments: dict[str, Any], lifetime: Lifetime) -> ports.EffectCall:
    return ports.EffectCall(member, arguments, LINEAGE, "refresh", lifetime, RELEASE_TIMEOUT)


def selector_ref(selector: str) -> SelectorRef:
    return SelectorRef(LINEAGE, "up", selector, datetime.now(UTC))


def _observe_host(built: core.Implementation, watched: bool = False) -> Any:
    """One `observe_host`; `watched` runs it under the V-5.1 watcher, which snapshots the process
    table twice per call: the reads a case is ABOUT are watched, its setup reads are not."""
    if watched:
        with read(built, "GrantReads.observe_host"):
            return built.impl.observe_host()
    return built.impl.observe_host()


def _in_consumer(built: core.Implementation, target: Any, watched: bool = False) -> Any:
    if watched:
        with read(built, "GrantReads.observe_in_consumer"):
            return built.impl.observe_in_consumer(target)
    return built.impl.observe_in_consumer(target)


def _refresh(built: core.Implementation) -> Any:
    return built.impl.refresh(_observe_host(built).found, refresh_ticket())


# ------------------------------------------------------------------------------- reads (B3-C8)


def host_reports_identity_expiry_generation_and_interactivity(built: core.Implementation) -> None:
    seen = _observe_host(built, watched=True)
    assert seen.code is None
    assert seen.identity == built.extras["identity"]
    assert seen.generation == built.extras["current_generation"]()
    assert seen.expires_at == built.extras["expires_at"]()
    assert seen.interactive_required is False
    assert seen.found.resource_kind == DEMO_CREDENTIAL_KIND  # the only value refresh accepts
    assert seen.found.selector == seen.identity
    built.extras["set_interactive"](True)
    assert _observe_host(built).interactive_required is True


def an_unreachable_issuer_sets_the_code_and_yields_no_currency(built: core.Implementation) -> None:
    built.extras["plant_consumer"]("c-one")
    built.extras["set_reachable"](False)
    seen = _observe_host(built, watched=True)
    assert seen.code == GRANT_ISSUER_UNREACHABLE  # never an exception, never a partial reading
    consumer = _in_consumer(built, selector_ref("c-one"))
    assert ports.grant_currency(consumer, seen) is None  # the join treats it as unknown (B3-C19)
    built.extras["set_reachable"](True)
    assert _observe_host(built).code is None


def host_reads_leave_no_trace(built: core.Implementation) -> None:
    before = _observe_host(built)
    for _ in range(2):  # repeated reads change nothing the watcher looks at (the issuer's state)
        assert _observe_host(built, watched=True).generation == before.generation
    built.extras["plant_consumer"]("c-one")
    for _ in range(2):
        _in_consumer(built, selector_ref("c-one"), watched=True)
    assert _observe_host(built).generation == before.generation


# --------------------------------------------------------------------- consumer currency (B3-C9)


def a_consumer_reports_authentication_and_the_generation_it_saw(built: core.Implementation) -> None:
    built.extras["plant_consumer"]("c-one")
    current = built.extras["current_generation"]()
    handle = CreatedHandle(LINEAGE, "up", "c-one", ports.InRunGroup())
    found = FoundRef("consumer", "c-one", datetime.now(UTC))
    for target in (selector_ref("c-one"), handle, found):  # every address form names one instance
        seen = _in_consumer(built, target, watched=isinstance(target, SelectorRef))
        assert seen.authenticated is True
        assert seen.generation_seen == current
        assert seen.code is None
    fact = ports.grant_currency(_in_consumer(built, selector_ref("c-one")), _observe_host(built))
    assert fact is not None and fact.observed_generation == current
    assert fact.valid_until == built.extras["expires_at"]() and fact.older is None


def stale_only_when_the_issuers_own_order_says_older(built: core.Implementation) -> None:
    extras = built.extras
    first = extras["current_generation"]()
    extras["plant_consumer"]("old")  # holds the first generation
    latest = extras["advance"]()
    while not latest < first:  # make the issuer's newest generation SORT below its first
        latest = extras["advance"]()
    extras["plant_consumer"]("new")  # holds the newest
    extras["plant_consumer"]("alien", NEVER_ISSUED)  # a name the issuer never issued
    old = _in_consumer(built, selector_ref("old"), watched=True)
    assert old.generation_seen == first and old.authenticated is True
    assert old.code == CREDENTIAL_STALE  # older by the issuer's order although it sorts above
    fact = ports.grant_currency(old, _observe_host(built))
    assert fact is not None and fact.older == CREDENTIAL_STALE and fact.observed_generation == first
    new = _in_consumer(built, selector_ref("new"))
    assert new.generation_seen == latest and new.code is None  # equal is not stale
    alien = _in_consumer(built, selector_ref("alien"))
    assert alien.generation_seen == NEVER_ISSUED and alien.authenticated is False
    assert alien.code is None  # mere inequality never sets it: nothing proves it older


def stale_needs_an_issuer_that_can_order_the_generations(built: core.Implementation) -> None:
    built.extras["plant_consumer"]("old")
    built.extras["advance"]()
    built.extras["set_reachable"](False)
    seen = _in_consumer(built, selector_ref("old"))
    assert seen.code is None and seen.authenticated is False  # cannot prove older, does not guess
    built.extras["set_reachable"](True)
    assert _in_consumer(built, selector_ref("old")).code == CREDENTIAL_STALE


def a_consumer_read_reaches_only_the_named_instance(built: core.Implementation) -> None:
    extras = built.extras
    extras["plant_consumer"]("one")
    older = extras["current_generation"]()
    newer = extras["advance"]()
    extras["plant_consumer"]("two")
    one = _in_consumer(built, selector_ref("one"))
    two = _in_consumer(built, selector_ref("two"))
    assert (one.generation_seen, one.code) == (older, CREDENTIAL_STALE)
    assert (two.generation_seen, two.code) == (newer, None)
    ghost = _in_consumer(built, selector_ref("ghost"))
    assert (ghost.authenticated, ghost.generation_seen, ghost.code) == (False, None, None)


def a_consumer_address_is_a_handle_a_found_ref_or_a_selector_ref(
    built: core.Implementation,
) -> None:
    for bad in ("one", 1, None):
        try:
            built.impl.observe_in_consumer(bad)
        except (ValueError, AttributeError):
            continue
        raise AssertionError(f"{bad!r} is not a consumer address")


# ------------------------------------------------------------------------- refresh (B3-C11, B3-E4)


def refresh_issues_a_new_generation_and_keeps_the_identity(built: core.Implementation) -> None:
    before = _observe_host(built)
    confirmation = built.impl.refresh(before.found, refresh_ticket())
    assert status(confirmation) is ConfirmationStatus.APPLIED
    assert confirmation.identity == before.identity and confirmation.code is None
    after = _observe_host(built)
    assert after.generation != before.generation
    assert after.generation == built.extras["current_generation"]()
    assert (after.identity, after.interactive_required) == (before.identity, False)
    built.extras["plant_consumer"]("held", before.generation)
    held = _in_consumer(built, selector_ref("held"))
    assert held.code == CREDENTIAL_STALE  # the new generation is later by the issuer's own order


def an_interactive_identity_is_not_applied_with_its_name_and_nothing_changes(
    built: core.Implementation,
) -> None:
    built.extras["set_interactive"](True)
    seen = _observe_host(built)
    assert seen.interactive_required is True
    confirmation = built.impl.refresh(seen.found, refresh_ticket())
    assert status(confirmation) is ConfirmationStatus.NOT_APPLIED
    assert confirmation.code == CREDENTIAL_INTERACTIVE  # B3-E4; the join classifies it (J-5a)
    assert confirmation.identity == built.extras["identity"]  # the subject the human action names
    assert _observe_host(built).generation == seen.generation  # no attempt at a human flow


def refresh_takes_only_the_observed_grant_under_its_own_ticket(built: core.Implementation) -> None:
    assert list(inspect.signature(built.impl.refresh).parameters) == ["grant", "ticket"]
    seen = _observe_host(built)
    other = FoundRef("docker_container", seen.identity, datetime.now(UTC))
    stale = FoundRef(DEMO_CREDENTIAL_KIND, "someone-else", datetime.now(UTC))
    wrong_ticket = ticket("refresh", EffectFacetClass.CREATE)
    for grant, made in (
        (other, refresh_ticket()),
        (stale, refresh_ticket()),
        (seen.found, wrong_ticket),
    ):
        try:
            built.impl.refresh(grant, made)
        except ValueError:
            continue  # a contract violation by the caller, raised before any machine call (B3-E1)
        raise AssertionError("a refresh accepted what observe_host did not give it")
    assert _observe_host(built).generation == seen.generation


def refresh_against_an_unreachable_issuer_is_not_applied_with_the_code(
    built: core.Implementation,
) -> None:
    seen = _observe_host(built)
    built.extras["set_reachable"](False)
    confirmation = built.impl.refresh(seen.found, refresh_ticket())
    assert status(confirmation) is ConfirmationStatus.NOT_APPLIED  # provably nothing landed
    assert confirmation.code == GRANT_ISSUER_UNREACHABLE
    built.extras["set_reachable"](True)
    assert _observe_host(built).generation == seen.generation


def refresh_descriptor_is_durable_host_and_equal_across_attempts(
    built: core.Implementation,
) -> None:
    found = _observe_host(built).found
    before = _observe_host(built).generation
    for lifetime in (Lifetime.DURABLE, Lifetime.RUN):  # a SafeStartFacet member is always Durable
        call = effect_call("refresh", {"grant": found}, lifetime)
        first = ports.as_descriptor(built.impl.release_descriptor(call))
        assert first == ports.Durable(ports.DurableOwner.HOST)
        assert ports.as_descriptor(built.impl.release_descriptor(call)) == first
    assert _observe_host(built).generation == before  # deriving a descriptor changes nothing


# ------------------------------------------------------------------------- values (WR-EVID-12)


def _leaves(value: Any) -> Iterator[Any]:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _leaves(getattr(value, field.name))
    elif isinstance(value, (tuple, list, frozenset, set)):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


def no_secret_is_representable_or_emitted(built: core.Implementation) -> None:
    extras = built.extras
    extras["plant_consumer"]("c-one")
    seen = _observe_host(built)
    values: list[Any] = [seen, _in_consumer(built, selector_ref("c-one")), _refresh(built)]
    extras["advance"]()
    values.append(_in_consumer(built, selector_ref("c-one")))
    extras["set_interactive"](True)
    values.append(_refresh(built))
    extras["set_reachable"](False)
    values.extend([_observe_host(built), built.impl.refresh(seen.found, refresh_ticket())])
    extras["set_reachable"](True)
    secrets = extras["secret_values"]()
    assert secrets and all(secrets)
    for value in values:
        for leaf in _leaves(value):
            # the only kinds a result may hold: a name, an instant, a flag, nothing
            assert leaf is None or isinstance(leaf, (str, bool, datetime)), (type(value), leaf)
            text = str(leaf)
            assert all(s not in text for s in secrets), (type(value).__name__, "holds a secret")
        assert all(s not in repr(value) for s in secrets)
    # and the pure conversions the contract fixes carry only names, an instant and a code
    fact = ports.grant_currency(values[1], seen)
    assert fact is not None
    assert all(s not in repr(fact) for s in secrets)


def values_stay_within_their_bounds(built: core.Implementation) -> None:
    built.extras["plant_consumer"]("c-one")
    seen = _observe_host(built)
    consumer = _in_consumer(built, selector_ref("c-one"))
    confirmation = _refresh(built)
    for text in (seen.identity, seen.generation, consumer.generation_seen, confirmation.identity):
        assert text is not None and len(text.encode("utf-8")) <= bounds.TOKEN_MAX
    assert seen.expires_at.tzinfo is not None  # an instant, not a naive wall-clock time


CASES = (
    core.Case(
        "host_reports_identity_expiry_generation_and_interactivity",
        host_reports_identity_expiry_generation_and_interactivity,
    ),
    core.Case(
        "an_unreachable_issuer_sets_the_code_and_yields_no_currency",
        an_unreachable_issuer_sets_the_code_and_yields_no_currency,
    ),
    core.Case("host_reads_leave_no_trace", host_reads_leave_no_trace),
    core.Case(
        "a_consumer_reports_authentication_and_the_generation_it_saw",
        a_consumer_reports_authentication_and_the_generation_it_saw,
    ),
    core.Case(
        "stale_only_when_the_issuers_own_order_says_older",
        stale_only_when_the_issuers_own_order_says_older,
    ),
    core.Case(
        "stale_needs_an_issuer_that_can_order_the_generations",
        stale_needs_an_issuer_that_can_order_the_generations,
    ),
    core.Case(
        "a_consumer_read_reaches_only_the_named_instance",
        a_consumer_read_reaches_only_the_named_instance,
    ),
    core.Case(
        "a_consumer_address_is_a_handle_a_found_ref_or_a_selector_ref",
        a_consumer_address_is_a_handle_a_found_ref_or_a_selector_ref,
    ),
    core.Case(
        "refresh_issues_a_new_generation_and_keeps_the_identity",
        refresh_issues_a_new_generation_and_keeps_the_identity,
    ),
    core.Case(
        "an_interactive_identity_is_not_applied_with_its_name_and_nothing_changes",
        an_interactive_identity_is_not_applied_with_its_name_and_nothing_changes,
    ),
    core.Case(
        "refresh_takes_only_the_observed_grant_under_its_own_ticket",
        refresh_takes_only_the_observed_grant_under_its_own_ticket,
    ),
    core.Case(
        "refresh_against_an_unreachable_issuer_is_not_applied_with_the_code",
        refresh_against_an_unreachable_issuer_is_not_applied_with_the_code,
    ),
    core.Case(
        "refresh_descriptor_is_durable_host_and_equal_across_attempts",
        refresh_descriptor_is_durable_host_and_equal_across_attempts,
    ),
    core.Case("no_secret_is_representable_or_emitted", no_secret_is_representable_or_emitted),
    core.Case("values_stay_within_their_bounds", values_stay_within_their_bounds),
)

core.register_family(FAMILY, CASES)


# ---------------------------------------------------------------- delivery (B3-C11, WR-ENV-13)


def deliver_ticket(attempt: int = 1) -> Any:
    return ticket("deliver", EffectFacetClass.OWNED, Lifetime.RUN, attempt)


def _seen_by(built: core.Implementation, handle: Any) -> Any:
    return built.extras["reads"].observe_in_consumer(selector_ref(handle.selector))


def delivery_refreshes_the_channel_in_place_without_recreate_or_restart(
    built: core.Implementation,
) -> None:
    x = built.extras
    handle = x["own_consumer"]("c-one")
    first = x["current_generation"]()
    assert x["channel_generation"](handle) == first
    incarnation = x["consumer_incarnation"](handle)
    environment = dict(x["consumer_environment"](handle))
    latest = x["advance"]()
    assert x["channel_generation"](handle) == first  # the host rotated; the channel did not follow
    stale = _seen_by(built, handle)
    assert stale.generation_seen == first and stale.code == CREDENTIAL_STALE
    confirmation = built.impl.deliver(handle, deliver_ticket())
    assert status(confirmation) is ConfirmationStatus.APPLIED
    assert confirmation.code is None and confirmation.identity == handle.selector
    assert x["channel_generation"](handle) == latest == x["current_generation"]()
    fresh = _seen_by(built, handle)
    assert (fresh.authenticated, fresh.generation_seen, fresh.code) == (True, latest, None)
    assert x["consumer_incarnation"](handle) == incarnation  # the same container or process
    assert dict(x["consumer_environment"](handle)) == environment  # no recreate, no new variable


def the_channel_is_a_file_and_no_credential_variable_exists_at_creation(
    built: core.Implementation,
) -> None:
    x = built.extras
    handle = x["own_consumer"]("c-one")
    assert x["channel_generation"](handle) is not None  # the refreshable file is there at creation
    secrets = x["secret_values"]()
    for key, value in x["consumer_environment"](handle).items():
        assert not CREDENTIAL_NAME.search(key), f"credential-shaped variable {key!r} (WR-ENV-13)"
        assert all(s not in value for s in secrets)


def delivery_carries_the_handles_recorded_descriptor_unchanged(built: core.Implementation) -> None:
    handle = built.extras["own_consumer"]("c-one")
    call = effect_call("deliver", {"consumer": handle}, Lifetime.RUN)
    first = ports.as_descriptor(built.impl.release_descriptor(call))
    assert first == ports.as_descriptor(handle.release)  # an owned member's release, unchanged
    assert ports.as_descriptor(built.impl.release_descriptor(call)) == first
    generation = built.extras["channel_generation"](handle)
    assert built.extras["channel_generation"](handle) == generation  # deriving changes nothing


def delivery_against_an_unreachable_issuer_is_not_applied_and_changes_nothing(
    built: core.Implementation,
) -> None:
    x = built.extras
    handle = x["own_consumer"]("c-one")
    first = x["current_generation"]()
    x["advance"]()
    x["set_reachable"](False)
    confirmation = built.impl.deliver(handle, deliver_ticket())
    assert status(confirmation) is ConfirmationStatus.NOT_APPLIED  # provably nothing landed
    assert confirmation.code == GRANT_ISSUER_UNREACHABLE
    assert x["channel_generation"](handle) == first
    x["set_reachable"](True)
    assert status(built.impl.deliver(handle, deliver_ticket())) is ConfirmationStatus.APPLIED
    assert x["channel_generation"](handle) == x["current_generation"]()


def delivery_is_repeatable_and_reaches_only_its_consumer(built: core.Implementation) -> None:
    x = built.extras
    one, two = x["own_consumer"]("c-one"), x["own_consumer"]("c-two")
    older = x["current_generation"]()
    latest = x["advance"]()
    for attempt in (1, 2):  # a superseding attempt converges on the same channel content
        done = built.impl.deliver(one, deliver_ticket(attempt))
        assert status(done) is ConfirmationStatus.APPLIED
        assert x["channel_generation"](one) == latest
    assert x["channel_generation"](two) == older  # the other consumer's channel is untouched
    assert _seen_by(built, two).code == CREDENTIAL_STALE


def delivery_takes_only_an_owned_handle_under_its_own_ticket(built: core.Implementation) -> None:
    x = built.extras
    assert list(inspect.signature(built.impl.deliver).parameters) == ["consumer", "ticket"]
    handle = x["own_consumer"]("c-one")
    first = x["channel_generation"](handle)
    x["advance"]()
    found = FoundRef("consumer", handle.selector, datetime.now(UTC))
    for consumer, made in (
        (found, deliver_ticket()),
        (selector_ref(handle.selector), deliver_ticket()),
        (handle, ticket("deliver", EffectFacetClass.CREATE)),
    ):
        try:
            built.impl.deliver(consumer, made)
        except ValueError:
            continue  # a contract violation by the caller, raised before any machine call (B3-E1)
        raise AssertionError("a delivery accepted what an owned member does not")
    assert x["channel_generation"](handle) == first


def delivery_to_a_consumer_without_a_channel_is_not_applied_and_makes_none(
    built: core.Implementation,
) -> None:
    x = built.extras
    ghost = x["unprovisioned_consumer"]("ghost")
    confirmation = built.impl.deliver(ghost, deliver_ticket())
    assert status(confirmation) is ConfirmationStatus.NOT_APPLIED
    assert (confirmation.code, confirmation.identity) == (None, None)
    assert x["channel_generation"](ghost) is None  # nothing was created in its place


def a_delivery_confirmation_holds_no_secret(built: core.Implementation) -> None:
    x = built.extras
    handle = x["own_consumer"]("c-one")
    x["advance"]()
    results = [built.impl.deliver(handle, deliver_ticket())]
    x["set_reachable"](False)
    results.append(built.impl.deliver(handle, deliver_ticket()))
    x["set_reachable"](True)
    results.append(built.impl.deliver(x["unprovisioned_consumer"]("ghost"), deliver_ticket()))
    secrets = x["secret_values"]()
    for value in results:
        for leaf in _leaves(value):
            assert leaf is None or isinstance(leaf, (str, bool, datetime))
            assert all(s not in str(leaf) for s in secrets)
        assert all(s not in repr(value) for s in secrets)


DELIVERY_CASES = (
    core.Case(
        "delivery_refreshes_the_channel_in_place_without_recreate_or_restart",
        delivery_refreshes_the_channel_in_place_without_recreate_or_restart,
    ),
    core.Case(
        "the_channel_is_a_file_and_no_credential_variable_exists_at_creation",
        the_channel_is_a_file_and_no_credential_variable_exists_at_creation,
    ),
    core.Case(
        "delivery_carries_the_handles_recorded_descriptor_unchanged",
        delivery_carries_the_handles_recorded_descriptor_unchanged,
    ),
    core.Case(
        "delivery_against_an_unreachable_issuer_is_not_applied_and_changes_nothing",
        delivery_against_an_unreachable_issuer_is_not_applied_and_changes_nothing,
    ),
    core.Case(
        "delivery_is_repeatable_and_reaches_only_its_consumer",
        delivery_is_repeatable_and_reaches_only_its_consumer,
    ),
    core.Case(
        "delivery_takes_only_an_owned_handle_under_its_own_ticket",
        delivery_takes_only_an_owned_handle_under_its_own_ticket,
    ),
    core.Case(
        "delivery_to_a_consumer_without_a_channel_is_not_applied_and_makes_none",
        delivery_to_a_consumer_without_a_channel_is_not_applied_and_makes_none,
    ),
    core.Case("a_delivery_confirmation_holds_no_secret", a_delivery_confirmation_holds_no_secret),
)

core.register_family(DELIVERY_FAMILY, DELIVERY_CASES)
