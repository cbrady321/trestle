"""Gated `ToolchainProvisioning` stub (L.RB-4.3; B3-C15, B3-C3, OQ-18, WR-ENV-16, D-1).

STUB-PROVEN and NEVER reached from a task: `StubToolchainProvisioning` runs no process, touches no
file and downloads nothing; it records each call it receives in `calls` so a proof can show a task
run made none. Both members are durable, host-scoped effects (`release_descriptor` is
`Durable(HOST)`, B3-C3) taking a `SAFE_START` ticket:

- `refresh_adoption` records the call and confirms `APPLIED` (the stub has no adoption tree to
  rebuild);
- `install_pinned` is registrable only when operator policy permits installs (OQ-18's default is
  none: a missing tool is BLOCKED): unless `permit_installs` is set it is `NOT_APPLIED` with
  `TOOLCHAIN_MISSING` before anything happens, and even when set the stub installs nothing.

Nothing in `tasks.py` imports this module, holds an instance of it or calls it: the only way to
provisioning is a caller that constructs one and calls it on purpose.

The adapter imports only the standard library and `trestle.workflow` (BFD-47).
"""

from __future__ import annotations

from typing import Final

from trestle.workflow.declarations import EffectFacetClass
from trestle.workflow.ports import (
    CatalogEntry,
    Durable,
    DurableOwner,
    EffectCall,
    ReleaseDescriptor,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import Confirmation, ConfirmationStatus

TOOLCHAIN_MISSING: Final = "execution.toolchain_missing"
MEMBERS: Final = ("refresh_adoption", "install_pinned")


class StubToolchainProvisioning:
    """A `ToolchainProvisioning` that installs nothing, and records what was asked of it."""

    def __init__(self, permit_installs: bool = False) -> None:
        self.permit_installs = permit_installs
        self.calls: list[tuple[str, str]] = []

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        if call.member not in MEMBERS:
            raise ValueError(f"the toolchain provisioning port has no effect {call.member!r}")
        return Durable(DurableOwner.HOST)  # a SafeStartFacet member is always Durable (B3-C3)

    def refresh_adoption(self, project: CatalogEntry, ticket: AttemptTicket) -> Confirmation:
        self._check(ticket)
        self.calls.append(("refresh_adoption", project))
        return Confirmation(ConfirmationStatus.APPLIED, None, project)

    def install_pinned(
        self, project: CatalogEntry, tool: str, ticket: AttemptTicket
    ) -> Confirmation:
        self._check(ticket)
        self.calls.append(("install_pinned", f"{project}/{tool}"))
        if not self.permit_installs:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, TOOLCHAIN_MISSING, tool)
        return Confirmation(ConfirmationStatus.APPLIED, None, tool)

    @staticmethod
    def _check(ticket: AttemptTicket) -> None:
        if ticket.facet is not EffectFacetClass.SAFE_START:
            raise ValueError("toolchain provisioning is a SAFE_START effect: say so in the ticket")
