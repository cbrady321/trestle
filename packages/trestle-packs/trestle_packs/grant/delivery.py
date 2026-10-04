"""`GrantDelivery` through a mounted refreshable file (L.RB-9.2; B3-C11, B3-C3, B3-C17, WR-ENV-13,
WR-EVID-12, D-9: the credential is a DEMO stand-in, never real).

A consumer's credential channel is a DIRECTORY on the host that the consumer mounts (a container
mounts it read-only at `CHANNEL_MOUNT`, a local app reads the same path), holding one file,
`credentials`. Never an environment variable: a variable is fixed at creation and a refresh would
need a recreate or restart (`WR-ENV-13`). `deliver` rewrites that one file IN PLACE (a temporary
file in the same directory, then an atomic rename), so the consumer sees the new generation on its
next read with the same container or process, and never a half-written file.

- `deliver(consumer, ticket)` takes an owned handle and its own `OWNED` ticket, fetches the demo
  token of the issuer's CURRENT generation and publishes it. `APPLIED` carries the consumer's
  selector as `identity`; `NOT_APPLIED` only when nothing changed (no channel directory for that
  consumer: it is never created here; the issuer did not answer: `GRANT_ISSUER_UNREACHABLE`).
- The recorded descriptor is the handle's own, unchanged (an owned member's release, B3-C3).
- The token is held in memory for the length of one call and written to the channel file. It is in
  no return value, no code, no log line and no record (WR-EVID-12); the confirmation holds names
  and a status.
- Only a run-scoped selector names a channel, so a selector can never point outside the channels
  root: a container's (`trwr-...`, `[a-z0-9_.-]`) or an owned local process's (`proc-<16 hex>`).
  A container mounts its channel (`channel_mount`, container selectors only); a local process is
  told where it is by `TRESTLE_CHANNEL_DIR` (`channel_env`, process selectors only), a variable
  that names the directory, never the credential.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

from trestle.workflow.declarations import EffectFacetClass
from trestle.workflow.ports import EffectCall, ReleaseDescriptor, as_descriptor
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import Confirmation, ConfirmationStatus, OwnedHandle

from trestle_packs.grant.demo import (
    GRANT_ISSUER_UNREACHABLE,
    IssuerClient,
    IssuerUnreachable,
    IssuerUnreadable,
)

CHANNEL_FILE: Final = "credentials"  # the refreshable file's name inside a channel directory
CHANNEL_MOUNT: Final = "/run/trestle-demo-credentials"  # where a container mounts the directory
SELECTOR_PREFIX: Final = "trwr-"  # MC-B-01: every object a run created is named trwr-<run>-<path>
CHANNEL_ENV: Final = "TRESTLE_CHANNEL_DIR"  # a local process's channel DIRECTORY, not a credential
_CONTAINER_SELECTOR = re.compile(r"trwr-[a-z0-9_.-]+")
_PROCESS_SELECTOR = re.compile(r"proc-[0-9a-f]{16}")  # `process.local.run_scoped_selector`
_APPLIED = ConfirmationStatus.APPLIED
_NOT_APPLIED = ConfirmationStatus.NOT_APPLIED


def channel_directory(root: Path, selector: str) -> Path:
    """The channel directory of the consumer a run-scoped `selector` names, under `root`."""
    if not (_CONTAINER_SELECTOR.fullmatch(selector) or _PROCESS_SELECTOR.fullmatch(selector)):
        raise ValueError(f"{selector!r} is not a run-scoped selector: it names no channel")
    return root / selector


def channel_mount(root: Path, selector: str) -> tuple[str, str]:
    """`(host source, container target)` for a consumer container's read-only bind mount."""
    if not _CONTAINER_SELECTOR.fullmatch(selector):
        raise ValueError(f"{selector!r} is not a run-scoped container selector: nothing mounts it")
    return str(channel_directory(root, selector)), CHANNEL_MOUNT


def channel_env(root: Path, selector: str) -> dict[str, str]:
    """The environment that tells an owned local process where its channel directory is. The
    process reads `credentials` inside it on every use, so a refresh needs no restart."""
    if not _PROCESS_SELECTOR.fullmatch(selector):
        raise ValueError(f"{selector!r} is not an owned local process selector: no channel env")
    return {CHANNEL_ENV: str(channel_directory(root, selector))}


def provision_channel(root: Path, selector: str) -> Path:
    """Create the (empty) channel directory a consumer will mount, before the consumer exists. The
    directory is world-readable and not writable by the consumer (it mounts it read-only)."""
    directory = channel_directory(root, selector)
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    return directory


def write_channel(directory: Path, token: str) -> None:
    """Publish `token` as the channel's `credentials` file: a temporary file next to it, then an
    atomic rename, so a reader sees the old file or the new one and never a partial one. The mode
    is 0644 because the token is a demo stand-in and a container's user is not the host's."""
    target = directory / CHANNEL_FILE
    scratch = directory / f".{CHANNEL_FILE}.{os.getpid()}.tmp"
    try:
        scratch.write_text(token, encoding="utf-8")
        scratch.chmod(0o644)
        os.replace(scratch, target)
    finally:
        scratch.unlink(missing_ok=True)


class ChannelDelivery:
    """`GrantDelivery` (structurally) over the stub issuer at a loopback URL and a channels root."""

    def __init__(self, issuer_url: str, channels: Path) -> None:
        self._client = IssuerClient(issuer_url)
        self._channels = channels

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        """An owned member's descriptor is the handle's recorded one, unchanged (B3-C3)."""
        if call.member != "deliver":
            raise ValueError(f"the grant delivery port has no effect {call.member!r} (B3-C11)")
        return as_descriptor(_owned(call.arguments["consumer"]).release)

    def deliver(self, consumer: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        """B3-C11: refresh the owned consumer's credential channel in place."""
        owned = _owned(consumer)
        if ticket.facet is not EffectFacetClass.OWNED:
            raise ValueError("deliver is an OWNED effect: its ticket must say so (B3-C11)")
        directory = channel_directory(self._channels, owned.selector)
        if not directory.is_dir():
            return Confirmation(_NOT_APPLIED, None, None)  # no channel: nothing to refresh in place
        try:
            token = self._client.get("/credential").get("token")
        except (IssuerUnreachable, IssuerUnreadable):
            return Confirmation(_NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)
        if not isinstance(token, str) or not token:
            return Confirmation(_NOT_APPLIED, GRANT_ISSUER_UNREACHABLE, None)
        try:
            write_channel(directory, token)
        except OSError:
            return Confirmation(_NOT_APPLIED, None, None)  # the rename never happened
        return Confirmation(_APPLIED, None, owned.selector)


def _owned(consumer: object) -> OwnedHandle:
    if not isinstance(consumer, OwnedHandle):
        raise ValueError("deliver takes the owned handle of the consumer, nothing else (B3-C11)")
    return consumer
