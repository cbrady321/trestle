"""Mise-backed `ToolchainResolver` (L.RB-4.2; B3-C12, B3-C17 (5), B3-E1, B3-E3, V-11, V-13, MC-25,
MC-B-06, SA-14). STUB-PROVEN: only the JSON subset the stub `mise` prints is exercised (D-1,
OPEN-MISE-HOST undecided).

`MiseToolchainResolver(mise, execution, projects)` resolves a project's declared pin to an
absolute executable EXACTLY, or returns `Unresolved`; it never falls through to a tool found on
the search path. It asks the operator-named mise binary (an absolute path; the search path is never
used to find it) ONE question through the injected `ExecutionPort` (stdin closed, environment
built from empty, attributable to the run, like every other adapter call)::

    <mise> ls --current --json <tool>

and reads the answer, `{"<tool>": [{"version", "requested_version", "install_path", "installed",
...}]}`. It never invokes `mise exec`, `mise run`, `mise x`, `mise install` or a mise shim, not even
to learn a version (B3-C12): `reported_version` is what the RESOLVED EXECUTABLE itself prints for
`--version`, run by its own absolute path (`<install_path>/bin/<tool> --version`).

Outcomes (a value, never an exception; the only codes are V-11's):

- the tool is not in the listing, no entry of it is installed, the executable is absent from its
  install path, the project is unknown, or the executable reports a version that is not the pin:
  `Unresolved(TOOLCHAIN_MISSING)`; nothing installs it (OQ-18);
- output that is not JSON, not an object of tool -> list of entries, an entry outside the window
  the resolver reads (`installed` not a boolean, no `version` or `install_path` text, a relative
  `install_path`), more than one installed current entry (the pin is not exact), mise not startable
  or ended before it answered, a non-zero exit, or an executable whose `--version` output holds no
  version: `Unresolved(TOOLCHAIN_INTERFACE_DRIFT)`, never a guessed resolution (B3-E3).
  (This port never rebuilds or compares an adoption tree, V-5.2, so it never returns
  `ADOPTION_STALE`.)

`pin_fingerprint` is the sha256 of the canonical JSON `{"pin": <requested_version>, "tool":
<tool>}`; `adoption_fingerprint` is the sha256 of the canonical JSON of the tool's INSTALLED
current entries as sorted `[tool, version, install_path]` triples (so the tool's install changing
changes it, and `toolchain_currency` then joins STALE, B3-C19). The fake
(`trestle_packs.fakes.toolchain`) restates the same rules over the same JSON; the conformance suite
runs unmodified against both.

The mise listing is bound to a project through `projects`: a catalog entry -> the allowlisted
environment that makes `mise ls --current` answer for that project (its configuration selection).
`envelope`, when bound, is the directory mise's cache and state go under (`MISE_CACHE_DIR`,
`MISE_STATE_DIR` -> `<envelope>/cache`, `<envelope>/state`: the incidental write set of a resolve,
`INCIDENTAL_WRITES['ToolchainResolver.resolve']`). Executor-chosen (the contract names none): the
environment keys above, the 60 s call bound, taking the first line of `--version` output and its
first dotted-number token as the version.

Known bound: the port hands an adapter only the console TAIL of a command, at most `TEXT_MAX` (512)
bytes (B3-C14), and the listing of every current tool is longer than that as soon as a project pins
two tools. So the question names the tool (`ls --current --json <tool>`, one entry), and the
adoption fingerprint is the tool's own, not the host's whole tree: the generation a
`HostScopeReads(TOOLCHAIN_INSTALLS)` reading must equal is a separate leaf's decision. A listing
that fills the excerpt and does not parse is `TOOLCHAIN_INTERFACE_DRIFT`, never a guess.

An adapter module imports only the standard library and `trestle.workflow` (BFD-47), so the
constants below restate the ones `trestle.common.plan` owns; the SA-03 drift test reads both.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.ports import (
    BoundCommand,
    CatalogEntry,
    ExecutionClass,
    ExecutionPort,
    InRunGroup,
    Resolved,
    Unresolved,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CancelSignal,
    ConfirmationStatus,
    Lineage,
    NodePath,
    StopCause,
)

# V-11 (MC-B-12): each NAME is the V-11 name, each value `<origin>.<snake>` (DM-16).
TOOLCHAIN_MISSING: Final = "execution.toolchain_missing"
TOOLCHAIN_INTERFACE_DRIFT: Final = "adapter.toolchain_interface_drift"

# V-13 bounds (restated; SA-03 reads both spellings).
NAME_MAX: Final = 128
TOKEN_MAX: Final = 256
EXEC_PATH_MAX: Final = 1024
HUMAN_ACTION_MAX: Final = 1024

LIST_ARGS: Final = ("ls", "--current", "--json")
VERSION_ARGS: Final = ("--version",)
CALL_BOUND: Final = timedelta(seconds=60)
_VERSION = re.compile(r"\d+(?:\.\d+)*")

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
class _Failed:
    """One call that read nothing: the port could not start it, ended it, or it exited non-zero."""

    reason: str


def _unresolved(code: str, tool: str, pin: str, action: str) -> Unresolved:
    return Unresolved(code, tool[:NAME_MAX], pin[:TOKEN_MAX], action[:HUMAN_ACTION_MAX])


def missing(tool: str, pin: str) -> Unresolved:
    what = f"{tool} {pin}".strip()
    return _unresolved(
        TOOLCHAIN_MISSING,
        tool,
        pin,
        f"Install the pinned tool {what} with your own toolchain manager (Trestle installs "
        "none), then re-send.",
    )


def drift(tool: str, pin: str, reason: str) -> Unresolved:
    return _unresolved(
        TOOLCHAIN_INTERFACE_DRIFT,
        tool,
        pin,
        f"The toolchain manager's answer for {tool} is outside the supported shape ({reason}); "
        "check the operator-pinned mise binary, then re-send.",
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def pin_fingerprint(tool: str, pin: str) -> str:
    return _digest({"pin": pin, "tool": tool})


def satisfies(pin: str, reported: str, listed: str) -> bool:
    """Whether the executable's own version is the pin: a numeric pin is a prefix on a version
    segment (`3.12` is `3.12.4`, never `3.1` or `3.120`); an alias pin (`lts`, `temurin-21`) is
    satisfied by the version the listing names for it."""
    if pin[:1].isdigit():
        return reported == pin or (reported.startswith(pin) and not reported[len(pin)].isalnum())
    return reported != "" and reported in listed


def _entries(document: Any) -> dict[str, list[dict[str, Any]]] | str:
    """The listing as tool -> entries, or the reason it is outside the window."""
    if not isinstance(document, dict):
        return "the listing is not an object of tool to entries"
    out: dict[str, list[dict[str, Any]]] = {}
    for name, entries in document.items():
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            return f"{str(name)[:NAME_MAX]}: not a list of entries"
        for entry in entries:
            if not isinstance(entry.get("installed"), bool):
                return f"{str(name)[:NAME_MAX]}: installed is not a boolean"
            if entry["installed"] and not (
                isinstance(entry.get("version"), str)
                and isinstance(entry.get("install_path"), str)
                and os.path.isabs(entry["install_path"])
            ):
                return f"{str(name)[:NAME_MAX]}: an installed entry lacks version or install_path"
        out[str(name)] = entries
    return out


def adoption_fingerprint(listing: Mapping[str, list[dict[str, Any]]], tool: str) -> str:
    installed = sorted(
        [name, str(e["version"]), str(e["install_path"])]
        for name, entries in listing.items()
        if name == tool
        for e in entries
        if e["installed"]
    )
    return _digest(installed)


def select(
    listing: Mapping[str, list[dict[str, Any]]], tool: str
) -> tuple[dict[str, Any] | None, str, Unresolved | None]:
    """The one installed current entry of `tool`, the pin it was requested at, or the refusal."""
    entries = listing.get(tool, [])
    pin = next((str(e["requested_version"]) for e in entries if e.get("requested_version")), "")
    installed = [e for e in entries if e["installed"]]
    if not installed:
        return None, pin, missing(tool, pin)
    if len(installed) > 1:
        return None, pin, drift(tool, pin, "more than one installed current version")
    entry = installed[0]
    return entry, str(entry.get("requested_version") or pin or entry["version"]), None


def executable_of(entry: Mapping[str, Any], tool: str) -> str:
    return os.path.join(str(entry["install_path"]), "bin", tool)


class MiseToolchainResolver:
    """`ToolchainResolver` over the operator's mise binary, keyed by project (catalog entry)."""

    def __init__(
        self,
        mise: str | os.PathLike[str],
        execution: ExecutionPort,
        projects: Mapping[CatalogEntry, Mapping[str, str]],
        *,
        envelope: str | os.PathLike[str] | None = None,
        cancel: CancelSignal | None = None,
        call_bound: timedelta = CALL_BOUND,
    ) -> None:
        text = os.fspath(mise)
        if not os.path.isabs(text):
            raise ValueError("the mise executable path must be absolute (B3-C12, B3-C14)")
        self.mise = text
        self.execution = execution
        self.projects = {name: dict(env) for name, env in projects.items()}
        self.envelope = None if envelope is None else os.fspath(envelope)
        self.cancel: CancelSignal = _NeverCancel() if cancel is None else cancel
        self.call_bound = call_bound

    def resolve(self, project: CatalogEntry, tool: str) -> Resolved | Unresolved:
        environment = self.projects.get(project)
        if environment is None or not tool or tool.startswith("-"):
            return missing(tool, "")  # an option-shaped name is never handed to the manager
        text = self._call(self.mise, (*LIST_ARGS, tool), self._environment(environment))
        if isinstance(text, _Failed):
            return drift(tool, "", text.reason)
        try:
            document: Any = json.loads(text)
        except ValueError:
            return drift(tool, "", "not JSON")
        listing = _entries(document)
        if isinstance(listing, str):
            return drift(tool, "", listing)
        entry, pin, refusal = select(listing, tool)
        if refusal is not None or entry is None:
            return refusal if refusal is not None else missing(tool, pin)
        executable = executable_of(entry, tool)
        if len(executable) > EXEC_PATH_MAX:
            return drift(tool, pin, "the executable path is over the bound")
        if not (os.path.isfile(executable) and os.access(executable, os.X_OK)):
            return missing(tool, pin)
        version = self._reported_version(executable)
        if version is None:
            return drift(tool, pin, "the executable reports no version")
        if not satisfies(pin, version, str(entry["version"])):
            return missing(tool, pin)
        return Resolved(
            executable=executable,
            reported_version=version[:TOKEN_MAX],
            pin_fingerprint=pin_fingerprint(tool, pin),
            adoption_fingerprint=adoption_fingerprint(listing, tool),
        )

    def _environment(self, project_environment: Mapping[str, str]) -> dict[str, str]:
        environment = dict(project_environment)
        if self.envelope is not None:
            environment["MISE_CACHE_DIR"] = os.path.join(self.envelope, "cache")
            environment["MISE_STATE_DIR"] = os.path.join(self.envelope, "state")
        return environment

    def _reported_version(self, executable: str) -> str | None:
        output = self._call(executable, VERSION_ARGS, {})
        if isinstance(output, _Failed):
            return None
        lines = output.strip().splitlines()
        match = _VERSION.search(lines[0]) if lines else None
        return match.group(0) if match else None

    def _call(
        self, executable: str, args: tuple[str, ...], environment: Mapping[str, str]
    ) -> str | _Failed:
        """Run `<executable> <args>` once through the port: its console tail, or why nothing was
        read."""
        command = BoundCommand(
            task="toolchain.read",
            argv=(executable, *args),
            environment=dict(environment),
            resolved=Resolved(executable, "", "", ""),
            reports_tests=False,
        )
        until = datetime.now(UTC) + self.call_bound
        confirmation, result = self.execution.run(command, _READ_TICKET, self.cancel, until)
        status = ConfirmationStatus(getattr(confirmation.status, "value", confirmation.status))
        if status is ConfirmationStatus.NOT_APPLIED or result is None:
            return _Failed("the executable did not start")
        klass = ExecutionClass(getattr(result.classification, "value", result.classification))
        if klass is ExecutionClass.INTERRUPTED:
            return _Failed("the read was ended early")
        if result.exit_status != 0:
            return _Failed(f"exit status {result.exit_status}")
        return str(result.excerpt)
