"""The demo grant adapter: `GrantReads` and `GrantRefresh` over the stub issuer (L.RB-9.1;
B3-C8, B3-C9, B3-C11, B3-C17, B3-E1, B3-E4, WR-EVID-12, WR-CON-1, D-9, MC-B-06, MC-B-12).

AWS is DEMO ONLY (D-9, WR-CON-1): the only credential source this adapter can reach is an issuer
URL on loopback that the caller names; it refuses any other host at construction, reads no profile,
environment variable or credential file of its own and starts no process. The stub issuer
(`tests/fixtures/stubs/stub_issuer.py`) is the whole of "the cloud".

- `observe_host` reads identity, expiry, generation and whether interactive sign-in is required.
  An issuer that cannot be reached or read sets `code` to `GRANT_ISSUER_UNREACHABLE` (the other
  fields then mean nothing, B3-C8). No secret is representable in the result: the value types
  carry names, an instant, a generation and two flags (B3-I5).
- `observe_in_consumer` asks an injected `ConsumerProbe` which generation the consumer's credential
  carries and whether one authenticated call from inside it succeeded, then sets `CREDENTIAL_STALE`
  ONLY when the issuer's own ordering (`GET /order`) says that generation is older than the
  issuer's current one. Inequality never sets it, and an order the issuer cannot give (an unknown
  generation, an issuer that does not answer) leaves the code `None` (B3-C9). The read makes no
  write of its own; a probe's incidental writes belong to the port's declared set.
- `refresh` is a non-interactive refresh at the issuer (a `SAFE_START` on the found host grant,
  `Durable(HOST)`). `interactive_required` gives `NOT_APPLIED(CREDENTIAL_INTERACTIVE)` carrying the
  identity's name and no attempt at a human flow (B3-C11, B3-E4). `NOT_APPLIED` is returned only
  when the request provably never landed (the issuer refused the connection, or said no); a
  request that may have landed is `UNKNOWN`.

The stable codes: `CREDENTIAL_STALE` and `CREDENTIAL_INTERACTIVE` are `trestle.workflow.codes`
values (the join reads them); `GRANT_ISSUER_UNREACHABLE` is spelled here as
`trestle.common.plan.vocabulary` spells it (the adapter imports only stdlib and `trestle.workflow`;
the SA-03 drift test for Slice B reads both).
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from trestle.workflow import codes as workflow_codes
from trestle.workflow.declarations import EffectFacetClass
from trestle.workflow.ports import (
    ConsumerCurrency,
    Durable,
    DurableOwner,
    EffectCall,
    GrantObservation,
    GrantReads,
    GrantRefresh,
    ReleaseDescriptor,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    OwnedHandle,
    SelectorRef,
)

CREDENTIAL_STALE: Final = workflow_codes.CREDENTIAL_STALE
CREDENTIAL_INTERACTIVE: Final = workflow_codes.CREDENTIAL_INTERACTIVE
GRANT_ISSUER_UNREACHABLE: Final = "adapter.grant_issuer_unreachable"

DEMO_CREDENTIAL_KIND: Final = "demo_credential"  # the FoundRef resource kind of the host grant
TOKEN_MAX: Final = 256  # V-13: identity and generation, encoded
# Executor-chosen (the contract names none): how long one issuer request may take before the read
# is could-not-observe and the effect UNKNOWN.
ISSUER_CALL_BOUND_S: Final = 10.0
_NEVER: Final = datetime(1970, 1, 1, tzinfo=UTC)  # `expires_at` of an observation with a code
_APPLIED = ConfirmationStatus.APPLIED
_NOT_APPLIED = ConfirmationStatus.NOT_APPLIED
_UNKNOWN = ConfirmationStatus.UNKNOWN


class IssuerUnreachable(Exception):
    """The issuer did not answer; a request sent to it never landed."""


class IssuerUnreadable(Exception):
    """The issuer answered with something that is not the reply this adapter reads."""


class IssuerAmbiguous(Exception):
    """A request may have landed at the issuer (sent, no complete reply)."""


def _loopback_only(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "http" or not host:
        raise ValueError("the demo issuer is an http URL on loopback (D-9, WR-CON-1)")
    if host != "localhost":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError(f"the demo issuer must be on loopback, not {host!r} (D-9, WR-CON-1)")
    return url.rstrip("/")


class IssuerClient:
    """One request at a time against the stub issuer, JSON in and out, every call bounded."""

    def __init__(self, url: str, bound_s: float = ISSUER_CALL_BOUND_S) -> None:
        self.url = _loopback_only(url)
        self._bound = bound_s

    def get(self, path: str, **query: str) -> dict[str, Any]:
        suffix = "?" + urllib.parse.urlencode(query) if query else ""
        try:
            status, body = self._send(urllib.request.Request(f"{self.url}{path}{suffix}"))
        except (IssuerAmbiguous, IssuerUnreachable) as error:
            raise IssuerUnreachable(str(error)) from error  # a read that fails is not "absent"
        if status != 200:
            raise IssuerUnreadable(f"{path}: status {status}")
        return body

    def post(self, path: str) -> tuple[int, dict[str, Any]]:
        """`(status, body)`. `IssuerUnreachable` only when the connection was refused before any
        byte was sent; any later failure is `IssuerAmbiguous` (the refresh may have landed)."""
        return self._send(urllib.request.Request(f"{self.url}{path}", data=b"", method="POST"))

    def _send(self, request: urllib.request.Request) -> tuple[int, dict[str, Any]]:
        try:
            with urllib.request.urlopen(request, timeout=self._bound) as reply:  # noqa: S310
                return reply.status, _json(reply.read())
        except urllib.error.HTTPError as error:
            return error.code, _json(error.read())
        except urllib.error.URLError as error:
            if isinstance(error.reason, ConnectionRefusedError):
                raise IssuerUnreachable("connection refused") from error
            raise IssuerAmbiguous(str(error.reason)) from error
        except (TimeoutError, ConnectionError, OSError) as error:
            raise IssuerAmbiguous(str(error)) from error


def _json(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw or b"{}")
    except ValueError as error:
        raise IssuerUnreadable("the reply is not JSON") from error
    if not isinstance(body, dict):
        raise IssuerUnreadable("the reply is not an object")
    return body


def _token(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > TOKEN_MAX:
        raise IssuerUnreadable(f"{key!r} is not a bounded name")
    return value


def read_host(client: IssuerClient) -> tuple[str, datetime, str, bool]:
    """`(identity, expires_at, generation, interactive_required)` from the issuer's `/host`."""
    body = client.get("/host")
    identity, generation = _token(body, "identity"), _token(body, "generation")
    try:
        expires = datetime.fromisoformat(str(body["expires_at"]))
    except (KeyError, ValueError) as error:
        raise IssuerUnreadable("expires_at is not an instant") from error
    interactive = body.get("interactive_required")
    if expires.tzinfo is None or not isinstance(interactive, bool):
        raise IssuerUnreadable("expires_at or interactive_required is malformed")
    return identity, expires, generation, interactive


@dataclass(frozen=True, slots=True)
class ProbeReading:
    """What a consumer-side authenticated call returned: names and a flag, never a credential."""

    authenticated: bool
    generation_seen: str | None


class ConsumerProbe(Protocol):
    """Makes one authenticated call from inside the consumer `selector` names (B3-C9)."""

    def read(self, selector: str) -> ProbeReading | None:
        """`None` when no consumer answers to that selector (nothing to observe from)."""
        ...


def selector_of(target: object) -> str:
    """The one selector any consumer address names: a handle, a found ref or an observed ref."""
    if not isinstance(target, (OwnedHandle, CreatedHandle, FoundRef, SelectorRef)):
        raise ValueError("a consumer is a handle, a FoundRef or a SelectorRef (B3-C9)")
    return target.selector


class DemoGrant:
    """`GrantReads` and `GrantRefresh` over the stub issuer at a loopback URL (structurally)."""

    def __init__(self, issuer_url: str, probe: ConsumerProbe | None = None) -> None:
        self._client = IssuerClient(issuer_url)
        self._probe = probe

    @property
    def issuer_url(self) -> str:
        return self._client.url

    def as_map(self) -> dict[type, object]:
        """Protocol type -> the one implementation, the map `FacetContext.ports` takes."""
        return {GrantReads: self, GrantRefresh: self}

    # ---- GrantReads

    def observe_host(self) -> GrantObservation:
        now = datetime.now(UTC)
        try:
            identity, expires, generation, interactive = read_host(self._client)
        except (IssuerUnreachable, IssuerUnreadable):
            unread = FoundRef(DEMO_CREDENTIAL_KIND, "", now)
            return GrantObservation("", _NEVER, "", False, unread, GRANT_ISSUER_UNREACHABLE)
        found = FoundRef(DEMO_CREDENTIAL_KIND, identity, now)
        return GrantObservation(identity, expires, generation, interactive, found, None)

    def observe_in_consumer(
        self, consumer: CreatedHandle | OwnedHandle | FoundRef | SelectorRef
    ) -> ConsumerCurrency:
        selector = selector_of(consumer)
        reading = None if self._probe is None else self._probe.read(selector)
        if reading is None:
            return ConsumerCurrency(False, None, None)
        seen = reading.generation_seen
        if seen is not None and (not seen or len(seen.encode("utf-8")) > TOKEN_MAX):
            seen = None
        return ConsumerCurrency(reading.authenticated, seen, self._stale_code(seen))

    def _stale_code(self, seen: str | None) -> str | None:
        """`CREDENTIAL_STALE` only when the issuer's own ordering says `seen` is older than its
        current generation (B3-C9); anything the issuer cannot say leaves it `None`."""
        if seen is None:
            return None
        try:
            current = _token(self._client.get("/host"), "generation")
            relation = self._client.get("/order", a=seen, b=current).get("relation")
        except (IssuerUnreachable, IssuerUnreadable):
            return None
        return CREDENTIAL_STALE if relation == "older" else None

    # ---- GrantRefresh

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        if call.member != "refresh":
            raise ValueError(f"the demo grant port has no effect {call.member!r} (B3-C11)")
        return Durable(DurableOwner.HOST)  # a SafeStartFacet member is always Durable (B3-C3)

    def refresh(self, grant: FoundRef, ticket: AttemptTicket) -> Confirmation:
        """B3-C11: non-interactive refresh at the stub issuer of the found host grant."""
        if not isinstance(grant, FoundRef) or grant.resource_kind != DEMO_CREDENTIAL_KIND:
            raise ValueError("refresh takes the FoundRef from observe_host and nothing else")
        if ticket.facet is not EffectFacetClass.SAFE_START:
            raise ValueError("refresh is a SAFE_START effect: its ticket must say so (B3-C11)")
        try:
            identity, _, _, interactive = read_host(self._client)
        except (IssuerUnreachable, IssuerUnreadable):
            return Confirmation(_NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)  # nothing was sent
        if identity != grant.selector:
            raise ValueError("the FoundRef does not name the issuer's identity (stale ref)")
        if interactive:
            return Confirmation(_NOT_APPLIED, CREDENTIAL_INTERACTIVE, identity)
        try:
            status, _ = self._client.post("/refresh")
        except IssuerUnreachable:
            return Confirmation(_NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)
        except (IssuerAmbiguous, IssuerUnreadable):
            return Confirmation(_UNKNOWN, None, None)
        if status == 200:
            return Confirmation(_APPLIED, None, identity)
        if status == 409:  # the identity became interactive between the read and the request
            return Confirmation(_NOT_APPLIED, CREDENTIAL_INTERACTIVE, identity)
        return Confirmation(_UNKNOWN, None, None)


__all__ = [
    "CREDENTIAL_INTERACTIVE",
    "CREDENTIAL_STALE",
    "DEMO_CREDENTIAL_KIND",
    "GRANT_ISSUER_UNREACHABLE",
    "ISSUER_CALL_BOUND_S",
    "ConsumerProbe",
    "DemoGrant",
    "IssuerAmbiguous",
    "IssuerClient",
    "IssuerUnreachable",
    "IssuerUnreadable",
    "ProbeReading",
    "read_host",
    "selector_of",
]
