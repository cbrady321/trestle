"""`HostScopeReads` for the demo credential (L.RB-9.3; B3-C18, V-9.6, V-9.7; D-9: demo only).

`DemoHostScope.read(DEMO_CREDENTIAL)` is the current generation of the host demo credential, read
through the grant port's own `observe_host` (the one place the issuer is asked): `(subject,
generation, observed_at)`, the entry a `HostScopeReading` holds and the join compares a consumer's
currency fact with (J-13a, J-21, J-25). A subject whose generation cannot be read (an issuer that
does not answer, the toolchain fingerprint, which is the toolchain resolver's to read) is
`HostScopeUnreadable(HOST_SCOPE_UNREADABLE)`, never a reading: a subject with no reading joins as
if it differed (V-9.7). The read is one read-only issuer call and writes nothing; the generation is
a name, never a secret (WR-EVID-12).

`readings()` is the whole `HostScopeReading` a loop is handed for the subjects it names.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Final

from trestle.workflow.declarations import HostScopeRef
from trestle.workflow.ports import GrantReads, HostScopeUnreadable
from trestle.workflow.values import HostScopeReading

# `trestle.common.plan.vocabulary` spells it the same (the adapter imports only stdlib and
# `trestle.workflow`; the SA-03 drift test for Slice B and a grant test pin both).
HOST_SCOPE_UNREADABLE: Final = "adapter.host_scope_unreadable"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DemoHostScope:
    """A `HostScopeReads` over a `GrantReads`: the demo credential's current generation."""

    def __init__(self, grant: GrantReads, now: Callable[[], datetime] = _utc_now) -> None:
        self._grant = grant
        self._now = now

    def read(
        self, subject: HostScopeRef
    ) -> tuple[HostScopeRef, str, datetime] | HostScopeUnreadable:
        if subject is not HostScopeRef.DEMO_CREDENTIAL:
            return HostScopeUnreadable(subject, HOST_SCOPE_UNREADABLE)
        seen = self._grant.observe_host()
        if seen.code is not None or not seen.generation:
            return HostScopeUnreadable(subject, HOST_SCOPE_UNREADABLE)
        return (subject, seen.generation, self._now())

    def readings(
        self, subjects: Iterable[HostScopeRef] = (HostScopeRef.DEMO_CREDENTIAL,)
    ) -> HostScopeReading:
        """The readings of every subject that could be read (an unreadable one has none)."""
        found = [r for s in subjects if not isinstance(r := self.read(s), HostScopeUnreadable)]
        return HostScopeReading(tuple(found))
