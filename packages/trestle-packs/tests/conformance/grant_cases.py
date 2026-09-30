"""The Demo Credential Serving family's read and refresh cases (L.RB-9.1; B3-C8, B3-C9, B3-C11,
B3-C17, B3-C19, B3-E1, B3-E4, WR-EVID-12, MC-B-06).

Registered through the suite core's `register_family` (L.SV-5.15) as family `grant` and run,
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

Generations are opaque names whose string order is NOT the order the issuer issued them in, so
only the issuer's own ordering can say "older" (B3-C9); the stale case makes a later generation
sort below the first. No case branches on which implementation it runs
against (`test_cases_unbranched`).
"""

from __future__ import annotations

import dataclasses
import inspect
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
