"""`HostScopeReads` for the toolchain installs (V-9.6, V-9.7; B3-C19; DEFERRED-DECISIONS Q3).

`ToolchainHostScope.read(TOOLCHAIN_INSTALLS)` is the toolchain's current adoption generation: the
resolver's `adoption_fingerprint` for the configured `(project, tool)`, the same value
`ports.toolchain_currency` puts in a currency fact, so a fact that is current joins equal.
A listing that cannot be read (`Unresolved`), no configured target, or any other subject is
`HostScopeUnreadable(HOST_SCOPE_UNREADABLE)`, never a reading: a subject with no reading joins as
if it differed (V-9.7).

Q3 as built: the reader is bound, so the loop has a source for this subject, but no leaf yet
emits a `TOOLCHAIN_INSTALLS` currency fact and nothing produces `ADOPTION_STALE`. Which tool's
fingerprint is "the host's" is that later leaf's decision (the resolver's module docstring); until
then a reference binding names no target and the subject reads as unreadable. The read is one
read-only resolver call and writes nothing outside the resolver's own envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from trestle.workflow.declarations import HostScopeRef
from trestle.workflow.ports import CatalogEntry, HostScopeUnreadable, ToolchainResolver, Unresolved

# `trestle.common.plan.vocabulary` spells it the same (adapters import only stdlib and
# `trestle.workflow`; `grant.host_scope` restates it too).
HOST_SCOPE_UNREADABLE: Final = "adapter.host_scope_unreadable"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ToolchainHostScope:
    """A `HostScopeReads` over a `ToolchainResolver`: the configured tool's adoption generation."""

    def __init__(
        self,
        resolver: ToolchainResolver,
        target: tuple[CatalogEntry, str] | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._resolver = resolver
        self._target = target
        self._now = now

    def read(
        self, subject: HostScopeRef
    ) -> tuple[HostScopeRef, str, datetime] | HostScopeUnreadable:
        if subject is not HostScopeRef.TOOLCHAIN_INSTALLS or self._target is None:
            return HostScopeUnreadable(subject, HOST_SCOPE_UNREADABLE)
        project, tool = self._target
        resolved = self._resolver.resolve(project, tool)
        if isinstance(resolved, Unresolved):
            return HostScopeUnreadable(subject, HOST_SCOPE_UNREADABLE)
        return (subject, resolved.adoption_fingerprint, self._now())
