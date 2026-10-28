"""Fake demo grant port (L.RB-9.1; B3-C8, B3-C9, B3-C11, B3-C17, WR-EVID-12, DM-09, D-9).

Stdlib only, like its siblings (a fake needs no `trestle` install): the values it returns carry the
field names of the B3 types they stand for (`GrantObservation`, `ConsumerCurrency`,
`Confirmation`), and a release descriptor is its wire mapping. `FakeGrant` implements `GrantReads`
and `GrantRefresh` (and, from L.RB-9.2, `GrantDelivery`) over an in-memory world: one demo identity,
the ordered generations its issuer has issued, an expiry, and consumers each holding one
generation. No secret exists in it: a consumer holds a generation NAME and the only
credential-shaped string, `secret_values()`, is what the never-capture proofs search for (it is
never returned by any member).

World controls the conformance suite uses (never members of the port): `plant_consumer`, `advance`
(the issuer moves to a new generation behind the port's back), `set_interactive`, `set_reachable`,
`current_generation`, `expires_at`, `secret_values`, and for delivery `channel_generation` (what
the consumer's mounted file holds), `incarnation` (changes only when the consumer is recreated or
restarted, which `deliver` never does), `recreate` and `environment` (a consumer's environment: it
never holds a credential).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from trestle_packs.fakes.command import Confirmation, ConfirmationStatus, _value, durable
from trestle_packs.fakes.marker import FoundRef

CREDENTIAL_STALE = "execution.credential_stale"
CREDENTIAL_INTERACTIVE = "execution.credential_interactive"
GRANT_ISSUER_UNREACHABLE = "adapter.grant_issuer_unreachable"
DEMO_CREDENTIAL_KIND = "demo_credential"
DEFAULT_LIFETIME = timedelta(hours=1)
_NEVER = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class GrantObservation:
    identity: str
    expires_at: datetime
    generation: str
    interactive_required: bool
    found: FoundRef
    code: str | None


@dataclass(frozen=True, slots=True)
class ConsumerCurrency:
    authenticated: bool
    generation_seen: str | None
    code: str | None


class FakeGrant:
    """A `GrantReads` + `GrantRefresh` over an in-memory issuer and consumers."""

    def __init__(self, identity: str = "demo-user", lifetime: timedelta = DEFAULT_LIFETIME) -> None:
        self.identity = identity
        self._lifetime = lifetime
        self._issued: list[str] = []
        self._expires_at = _NEVER
        self._interactive = False
        self._reachable = True
        self._consumers: dict[str, str] = {}
        self._incarnations: dict[str, int] = {}
        self._nonce = "fake-nonce-0000"
        self.refreshes = 0
        self.advance()

    # ------------------------------------------------------------------ the world (suite controls)

    def advance(self) -> str:
        """The issuer issues a new generation (a host rotation)."""
        n = len(self._issued) + 1
        generation = f"gen-{(n * 7919) % 100003:05d}"  # opaque: string order is not issue order
        self._issued.append(generation)
        self._expires_at = datetime.now(UTC) + self._lifetime
        return generation

    def current_generation(self) -> str:
        return self._issued[-1]

    def expires_at(self) -> datetime:
        return self._expires_at

    def set_interactive(self, value: bool) -> None:
        self._interactive = value

    def set_reachable(self, value: bool) -> None:
        self._reachable = value

    def plant_consumer(self, selector: str, generation: str | None = None) -> None:
        """A consumer named `selector` holding `generation` (default: the current one; a name the
        issuer never issued is allowed)."""
        self._consumers[selector] = generation or self.current_generation()
        self._incarnations.setdefault(selector, 1)

    def channel_generation(self, selector: str) -> str | None:
        """What the consumer's mounted credentials file holds (None: no such consumer)."""
        return self._consumers.get(selector)

    def incarnation(self, selector: str) -> int:
        return self._incarnations[selector]

    def recreate(self, selector: str) -> None:
        """The consumer is recreated (a fresh instance): how the world tells it from a refresh."""
        self._incarnations[selector] += 1

    def environment(self, selector: str) -> dict[str, str]:
        return {"PATH": "/usr/bin"}  # a consumer's environment: never a credential

    def secret_values(self) -> tuple[str, ...]:
        return (self._nonce, *(f"demo-token:{g}:{self._nonce}" for g in self._issued))

    def _order(self, a: str, b: str) -> str:
        if a not in self._issued or b not in self._issued:
            return "unknown"
        ia, ib = self._issued.index(a), self._issued.index(b)
        return "older" if ia < ib else "newer" if ia > ib else "same"

    # ------------------------------------------------------------------ GrantReads

    def observe_host(self) -> GrantObservation:
        now = datetime.now(UTC)
        if not self._reachable:
            unread = FoundRef(DEMO_CREDENTIAL_KIND, "", now)
            return GrantObservation("", _NEVER, "", False, unread, GRANT_ISSUER_UNREACHABLE)
        found = FoundRef(DEMO_CREDENTIAL_KIND, self.identity, now)
        return GrantObservation(
            self.identity,
            self._expires_at,
            self.current_generation(),
            self._interactive,
            found,
            None,
        )

    def observe_in_consumer(self, consumer: Any) -> ConsumerCurrency:
        selector = consumer.selector  # a handle, a FoundRef or a SelectorRef: all name one
        seen = self._consumers.get(selector)
        if seen is None:
            return ConsumerCurrency(False, None, None)
        authenticated = (
            self._reachable and seen in self._issued and datetime.now(UTC) < self._expires_at
        )
        stale = self._reachable and self._order(seen, self.current_generation()) == "older"
        return ConsumerCurrency(authenticated, seen, CREDENTIAL_STALE if stale else None)

    # ------------------------------------------------------------------ GrantRefresh

    def release_descriptor(self, call: Any) -> Any:
        if call.member == "deliver":  # an owned member's descriptor is the handle's own (B3-C3)
            return call.arguments["consumer"].release
        if call.member != "refresh":
            raise ValueError(f"the demo grant port has no effect {call.member!r} (B3-C11)")
        return durable("host")  # a SafeStartFacet member is always Durable (B3-C3)

    def refresh(self, grant: Any, ticket: Any) -> Confirmation:
        if getattr(grant, "resource_kind", None) != DEMO_CREDENTIAL_KIND:
            raise ValueError("refresh takes the FoundRef from observe_host and nothing else")
        if _value(ticket.facet) != "safe_start":
            raise ValueError("refresh is a SAFE_START effect: its ticket must say so (B3-C11)")
        if not self._reachable:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)
        if grant.selector != self.identity:
            raise ValueError("the FoundRef does not name the issuer's identity (stale ref)")
        if self._interactive:
            return Confirmation(
                ConfirmationStatus.NOT_APPLIED, CREDENTIAL_INTERACTIVE, self.identity
            )
        self.advance()
        self.refreshes += 1
        return Confirmation(ConfirmationStatus.APPLIED, None, self.identity)

    # ------------------------------------------------------------------ GrantDelivery

    def deliver(self, consumer: Any, ticket: Any) -> Confirmation:
        """Refresh the consumer's channel in place: same instance, the current generation."""
        if not hasattr(consumer, "release") or not hasattr(consumer, "lineage"):
            raise ValueError("deliver takes the owned handle of the consumer, nothing else")
        if _value(ticket.facet) != "owned":
            raise ValueError("deliver is an OWNED effect: its ticket must say so (B3-C11)")
        if consumer.selector not in self._consumers:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)  # no such channel
        if not self._reachable:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)
        self._consumers[consumer.selector] = self.current_generation()
        return Confirmation(ConfirmationStatus.APPLIED, None, consumer.selector)
