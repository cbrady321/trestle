"""A credential consumer that is a LOCAL app, and the delivery that refreshes it in place
(L.RB-9.4.fix1; WR-ENV-13:refresh-in-place-local-app; not a test module).

DEVIATION (recorded in B-HOST2-RETURN, orchestrator decision): the product `ChannelDelivery`
addresses only run-scoped container selectors (`trwr-...`, `channel_directory` refuses anything
else), and a local process's selector is `proc-<hex>`, so nothing in the product can refresh a local
app's credential in place. The `[local_app]` case is proved on a rig with a test-defined delivery
binding, `LocalAppDelivery`, that does what `ChannelDelivery` does for a local process: it rewrites
the app's credentials file in place (temporary file, atomic rename, the pack's own `write_channel`)
with the issuer's CURRENT token. It addresses an owned `proc-` selector only, so a found process is
never delivered to. A product delivery for local processes would replace it.

`LocalConsumerUnit` is L.RB-9.5's `ConsumerUnit` over an agent-launched spec (a real
`LocalProcessPort` process, the stdlib app in `never` mode: it only has to stay alive), declared
with resource kind `local_process`. Shared by `host/test_credential_currency.py` and its twin.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from pathlib import Path
from typing import Any, Final

from trestle.workflow import codes, ports
from trestle.workflow.declarations import LeafDeclaration, RealizationKind
from trestle.workflow.ports import BoundCommand, Resolved, ResourceSpec, as_descriptor
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import Confirmation, ConfirmationStatus, Lineage, NodePath, OwnedHandle
from trestle_packs.grant import CHANNEL_FILE, IssuerClient, write_channel
from trestle_packs.grant.demo import GRANT_ISSUER_UNREACHABLE, IssuerUnreachable, IssuerUnreadable
from trestle_packs.process.local import LocalProcessPort, run_scoped_selector

from trestle_env import tree
from twin import consumers
from twin.local_app import APP, AwaitListening

UNIT: Final = "consumer.current"
_OWNED_PROCESS: Final = re.compile(r"proc-[0-9a-f]{16}")


def selector_for(run_id: str) -> str:
    """The owned consumer's selector, known before the run (it depends on `(lineage, effect)`)."""
    return run_scoped_selector(Lineage(run_id, NodePath((UNIT,))), consumers.UP)


def app_command(port: int, log: Path) -> BoundCommand:
    return BoundCommand(
        "consumer",
        (sys.executable, str(APP), "never"),
        {"PORT": str(port), "APP_EVENT_LOG": str(log), "PATH": "/usr/bin:/bin"},
        Resolved(sys.executable, "3.12", "pin", "adoption"),
        False,
    )


class LocalConsumerUnit(consumers.ConsumerUnit):
    """The credential consumer, a local process this run launches."""

    def __init__(self, command: BoundCommand) -> None:
        super().__init__(UNIT, "consumer")
        self._spec = ResourceSpec(
            "consumer", RealizationKind.AGENT_LAUNCHED_PROJECT, consumers.CONSUMER_ENTRY, command
        )

    def declare(self) -> LeafDeclaration:
        kind = tree.RESOURCE_KINDS[RealizationKind.AGENT_LAUNCHED_PROJECT]
        return dataclasses.replace(
            super().declare(), resource_kind=kind, may_touch=frozenset({kind})
        )


class LocalAppDelivery:
    """`GrantDelivery` for an owned local process: the credentials file at
    `<channels>/<selector>/credentials`, rewritten in place with the issuer's current token."""

    def __init__(self, issuer_url: str, channels: Path) -> None:
        self._client = IssuerClient(issuer_url)
        self._channels = channels
        self.delivered: list[str] = []

    def channel(self, selector: str) -> Path:
        if not _OWNED_PROCESS.fullmatch(selector):
            raise ValueError(f"{selector!r} is not an owned local process: it names no channel")
        return self._channels / selector

    def release_descriptor(self, call: Any) -> Any:
        if call.member != "deliver":
            raise ValueError(f"the grant delivery port has no effect {call.member!r} (B3-C11)")
        return as_descriptor(call.arguments["consumer"].release)

    def deliver(self, consumer: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        if not isinstance(consumer, OwnedHandle):
            raise ValueError("deliver takes the owned handle of the consumer, nothing else")
        directory = self.channel(consumer.selector)
        if not directory.is_dir():
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        try:
            token = self._client.get("/credential").get("token")
        except (IssuerUnreachable, IssuerUnreadable):
            return Confirmation(ConfirmationStatus.NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)
        if not isinstance(token, str) or not token:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)
        write_channel(directory, token)
        self.delivered.append(consumer.selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, consumer.selector)


class Watched:
    """The local port, watched: `rotate` runs right after the consumer is created (the host's
    credential rotates while the run waits), every observation of the owned process records its
    identity (pid, start), and a restart or recreate is recorded."""

    def __init__(
        self, inner: LocalProcessPort | AwaitListening, selector: str, rotate: Any = None
    ) -> None:
        self._inner, self._selector, self._rotate = inner, selector, rotate
        self.seen: list[tuple[int, Any]] = []
        self.repairs: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create(self, spec: Any, ticket: Any) -> Any:
        answer = self._inner.create(spec, ticket)
        if self._rotate is not None:
            self._rotate()
        return answer

    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        answer = self._inner.observe(spec, lineage, effect)
        instance = self._inner._instances.get(self._selector)  # noqa: SLF001 (the rig's witness)
        if answer.selector_present and instance is not None:
            self.seen.append((instance.proc.pid, instance.start))
        return answer

    def restart(self, target: Any, ticket: Any) -> Any:
        self.repairs.append("restart")
        return self._inner.restart(target, ticket)

    def recreate(self, target: Any, ticket: Any) -> Any:
        self.repairs.append("recreate")
        return self._inner.recreate(target, ticket)


def port_map(watched: Watched, grant: Any, delivery: Any) -> dict[type, object]:
    return {
        ports.ResourceReads: watched,
        ports.ResourceCreate: watched,
        ports.ResourceOwned: watched,
        ports.GrantReads: grant,
        ports.GrantDelivery: delivery,
    }


def stale_remedy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r.get("path") == UNIT and r["class"] == "issue" and r["effect"] == consumers.DELIVER
        if r["remedy"]["code"] == codes.CREDENTIAL_STALE
    ]


__all__ = ["CHANNEL_FILE", "LocalAppDelivery", "LocalConsumerUnit", "Watched"]
