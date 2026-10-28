"""`GrantReads.observe_in_consumer`'s probes: the authenticated call made from inside a consumer
(L.RB-9.2; B3-C9, B3-C17, B3-C14, WR-VERIFY-6, D-9: DEMO only).

A probe answers for ONE consumer, the instance a selector names, and returns names and a flag
(`ProbeReading`), never a credential. The adapter starts no process of its own: a probe runs its
one command through an injected `ExecutionPort` (`ArgvRunner`), an absolute executable, an
environment built from empty, stdin closed, the process attributable to the run (B3-C14).

- `ContainerExecProbe`: `docker [--host E] exec <selector> sh -c <script> ...` runs INSIDE the
  container. The script reads the mounted credentials file, presents its token to the issuer with
  the container's own `wget` (busybox has it, `python` is not assumed) and prints
  `authenticated=<0|1> generation=<name>`; the token never leaves the container and is not in the
  host argv. `docker exec` addresses the container by exact name, so a selector reaches only the
  instance it names (B3-C17). A container that is not there gives no reading (`None`).
- `LocalAppProbe`: the local app is a process on the host whose credentials file is at a path the
  caller maps from the selector; the probe runs the demo client `stub_cloud sts get-caller-identity`
  with that file, the way an AWS SDK reads `AWS_SHARED_CREDENTIALS_FILE`.

Both write nothing themselves (an SDK's token cache would be `INCIDENTAL_WRITES`'s consumer
ephemeral region).
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.ports import (
    BoundCommand,
    ExecutionClass,
    ExecutionPort,
    InRunGroup,
    Resolved,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CancelSignal,
    ConfirmationStatus,
    Lineage,
    NodePath,
    StopCause,
)

from trestle_packs.grant.delivery import CHANNEL_FILE, CHANNEL_MOUNT
from trestle_packs.grant.demo import ProbeReading

# Executor-chosen (the contract names none): how long one probe command may run before the port
# ends it (EXECUTION_DEADLINE) and the read reports no reading.
PROBE_BOUND: Final = timedelta(seconds=30)
NO_CHANNEL: Final = 3  # the probe script's exit status when the credentials file is not there
NOT_AUTHENTICATED: Final = 254  # `stub_cloud`'s exit status for a token the issuer refuses
NO_CREDENTIALS: Final = 255  # `stub_cloud`'s exit status without credentials or an issuer
# What a consumer inside a container may call the issuer by (Docker Desktop's host alias) or, for a
# local app, loopback: a demo issuer is never anywhere else (D-9, WR-CON-1).
_ISSUER_HOSTS: Final = frozenset({"host.docker.internal", "localhost", "127.0.0.1", "::1"})
_READING = re.compile(r"authenticated=([01]) generation=(\S*)")

# `sh` (busybox ash) script: $1 issuer URL, $2 credentials file. Reads the token into a shell
# variable, prints only the generation the token claims and whether the issuer accepted it.
PROBE_SCRIPT: Final = (
    'token=$(cat "$2") || exit 3; '
    "gen=$(printf '%s' \"$token\" | cut -d: -f2); "
    'if wget -q -O /dev/null --header "Authorization: Bearer $token" "$1/whoami"; '
    "then ok=1; else ok=0; fi; "
    'printf \'authenticated=%s generation=%s\\n\' "$ok" "$gen"'
)

_READ_TICKET: Final = AttemptTicket(
    lineage=Lineage("", NodePath(())),
    effect="",
    facet=EffectFacetClass.EVENT,
    attempt=1,
    repeat=Repeat.SAFE,
    lifetime=Lifetime.RUN,
    release=InRunGroup(),
    remedy=None,
)  # a read has no attempt ticket (V-5.1); a real ExecutionPort takes the parameter and ignores it


class _NeverCancel:
    @property
    def requested(self) -> bool:
        return False

    def cause(self) -> StopCause | None:
        return None

    def wait(self, timeout: timedelta) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Ran:
    """What one probe command came to: `started` false when nothing ran; `exit_status` is the
    process's (None when it was ended); `output` the console tail."""

    started: bool
    exit_status: int | None
    output: str


class ArgvRunner:
    """Runs one absolute argv through the injected `ExecutionPort`: bounded, given environment."""

    def __init__(
        self,
        execution: ExecutionPort,
        cancel: CancelSignal | None = None,
        call_bound: timedelta = PROBE_BOUND,
    ) -> None:
        self._execution = execution
        self._cancel: CancelSignal = _NeverCancel() if cancel is None else cancel
        self._bound = call_bound

    def run(self, argv: Sequence[str], environment: Mapping[str, str] | None = None) -> Ran:
        if not argv or not os.path.isabs(argv[0]):
            raise ValueError("a probe command's executable must be an absolute path (B3-C14)")
        command = BoundCommand(
            task="grant.probe",
            argv=tuple(argv),
            environment=dict(environment or {}),
            resolved=Resolved(argv[0], "", "", ""),
            reports_tests=False,
        )
        until = datetime.now(UTC) + self._bound
        confirmation, result = self._execution.run(command, _READ_TICKET, self._cancel, until)
        status = ConfirmationStatus(getattr(confirmation.status, "value", confirmation.status))
        if status is ConfirmationStatus.NOT_APPLIED or result is None:
            return Ran(False, None, "")
        klass = ExecutionClass(getattr(result.classification, "value", result.classification))
        if klass is ExecutionClass.INTERRUPTED:
            return Ran(True, None, result.excerpt)
        return Ran(True, result.exit_status, result.excerpt)


def _issuer_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or (parsed.hostname or "") not in _ISSUER_HOSTS:
        raise ValueError(f"a demo issuer is http on loopback or host.docker.internal: {url!r}")
    return url.rstrip("/")


class ContainerExecProbe:
    """One authenticated call from inside the container a selector names, by `docker exec`."""

    def __init__(
        self,
        docker: str,
        endpoint: str | None,
        runner: ArgvRunner,
        issuer_url: str,
        credentials_path: str = f"{CHANNEL_MOUNT}/{CHANNEL_FILE}",
    ) -> None:
        if not os.path.isabs(docker):
            raise ValueError("the docker executable path must be absolute (B3-C14)")
        self._docker, self._endpoint, self._runner = docker, endpoint, runner
        self._issuer_url = _issuer_url(issuer_url)
        self._credentials = credentials_path

    def argv(self, selector: str) -> tuple[str, ...]:
        host = ("--host", self._endpoint) if self._endpoint else ()
        return (
            self._docker,
            *host,
            "exec",
            selector,
            "sh",
            "-c",
            PROBE_SCRIPT,
            "probe",
            self._issuer_url,
            self._credentials,
        )

    def read(self, selector: str) -> ProbeReading | None:
        ran = self._runner.run(self.argv(selector))
        if not ran.started or ran.exit_status is None:
            return None
        if ran.exit_status == NO_CHANNEL:  # the container is there, its channel file is not
            return ProbeReading(False, None)
        match = _READING.search(ran.output) if ran.exit_status == 0 else None
        if match is None:  # docker could not exec (no such container, not running) or a bad reply
            return None
        return ProbeReading(match.group(1) == "1", match.group(2) or None)


class LocalAppProbe:
    """One authenticated call from a local app, run as `stub_cloud sts get-caller-identity` with
    the credentials file `credentials_of(selector)` maps to (`None`: no such consumer)."""

    def __init__(
        self,
        python: str,
        stub_cloud: str,
        issuer_url: str,
        credentials_of: Callable[[str], Path | None],
        runner: ArgvRunner,
    ) -> None:
        self._argv = (python, stub_cloud, "sts", "get-caller-identity")
        self._issuer_url = _issuer_url(issuer_url)
        self._credentials_of = credentials_of
        self._runner = runner

    def read(self, selector: str) -> ProbeReading | None:
        path = self._credentials_of(selector)
        if path is None or not path.is_file():
            return None
        ran = self._runner.run(
            self._argv,
            {"STUB_CLOUD_CREDENTIALS_FILE": str(path), "STUB_CLOUD_ENDPOINT_URL": self._issuer_url},
        )
        if not ran.started or ran.exit_status is None:
            return None
        body = _last_object(ran.output)
        generation = body.get("Generation") if body else None
        seen = generation if isinstance(generation, str) and generation else None
        if ran.exit_status == 0 and body and seen is not None:
            return ProbeReading(True, seen)
        if ran.exit_status in (NOT_AUTHENTICATED, NO_CREDENTIALS):
            return ProbeReading(False, seen)
        return None


def _last_object(text: str) -> dict[str, object] | None:
    for line in reversed(text.strip().splitlines()):
        try:
            body = json.loads(line)
        except ValueError:
            continue
        if isinstance(body, dict):
            return body
    return None


__all__ = [
    "PROBE_SCRIPT",
    "ArgvRunner",
    "ContainerExecProbe",
    "LocalAppProbe",
    "Ran",
]
