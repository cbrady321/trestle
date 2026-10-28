"""Fake toolchain resolver (L.RB-4.2; B3-C12, B3-C17, B3-E3, MC-B-06, MC-25, DM-09).

Stdlib only, like its siblings (a fake needs no `trestle` install): the values it returns carry the
field names of the B3 types they stand for (`Resolved`, `Unresolved`). `FakeToolchainResolver`
implements `ToolchainResolver` over what a mise-shaped tool prints: for each project a `listing`
callable returning the text `mise ls --current --json` would print (the conformance rig builds it
from the stub `stub_mise`, so the fake and the real resolver read the same JSON). It applies the
rules `trestle_packs.toolchain.resolver` applies, restated (a fake imports nothing of the adapter):
an installed entry of the tool whose executable exists under its install path resolves, with the
version the entry names standing for what the executable reports; a tool that is not listed, not
installed, whose executable is absent or whose version is not the pin is
`Unresolved(TOOLCHAIN_MISSING)`; text that is not the documented JSON shape is
`Unresolved(TOOLCHAIN_INTERFACE_DRIFT)`. It never consults `PATH`, never runs anything, and
records each question it asked the manager in `calls` (the argv `mise` would have received,
`ls --current --json <tool>`).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

TOOLCHAIN_MISSING = "execution.toolchain_missing"
TOOLCHAIN_INTERFACE_DRIFT = "adapter.toolchain_interface_drift"
NAME_MAX = 128
TOKEN_MAX = 256
HUMAN_ACTION_MAX = 1024
LIST_ARGS = ("ls", "--current", "--json")


@dataclass(frozen=True, slots=True)
class Resolved:
    executable: str
    reported_version: str
    pin_fingerprint: str
    adoption_fingerprint: str


@dataclass(frozen=True, slots=True)
class Unresolved:
    code: str
    tool: str
    pin: str
    human_action: str


def _unresolved(code: str, tool: str, pin: str, action: str) -> Unresolved:
    return Unresolved(code, tool[:NAME_MAX], pin[:TOKEN_MAX], action[:HUMAN_ACTION_MAX])


def _missing(tool: str, pin: str) -> Unresolved:
    what = f"{tool} {pin}".strip()
    return _unresolved(
        TOOLCHAIN_MISSING,
        tool,
        pin,
        f"Install the pinned tool {what} with your own toolchain manager (Trestle installs "
        "none), then re-send.",
    )


def _drift(tool: str, pin: str, reason: str) -> Unresolved:
    return _unresolved(
        TOOLCHAIN_INTERFACE_DRIFT,
        tool,
        pin,
        f"The toolchain manager's answer for {tool} is outside the supported shape ({reason}); "
        "check the operator-pinned mise binary, then re-send.",
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _satisfies(pin: str, reported: str, listed: str) -> bool:
    if pin[:1].isdigit():
        return reported == pin or (reported.startswith(pin) and not reported[len(pin)].isalnum())
    return reported != "" and reported in listed


def _listing(document: Any) -> dict[str, list[dict[str, Any]]] | str:
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


class FakeToolchainResolver:
    """A `ToolchainResolver` over per-project listings of what a mise-shaped tool prints."""

    def __init__(self, projects: Mapping[str, Callable[[], str]]) -> None:
        self.projects = dict(projects)
        self.calls: list[tuple[str, ...]] = []

    def resolve(self, project: str, tool: str) -> Resolved | Unresolved:
        listing_text = self.projects.get(project)
        if listing_text is None or not tool or tool.startswith("-"):
            return _missing(tool, "")
        self.calls.append((*LIST_ARGS, tool))
        try:
            document: Any = json.loads(listing_text())
        except ValueError:
            return _drift(tool, "", "not JSON")
        listing = _listing(document)
        if isinstance(listing, str):
            return _drift(tool, "", listing)
        entries = listing.get(tool, [])
        pin = next((str(e["requested_version"]) for e in entries if e.get("requested_version")), "")
        installed = [e for e in entries if e["installed"]]
        if not installed:
            return _missing(tool, pin)
        if len(installed) > 1:
            return _drift(tool, pin, "more than one installed current version")
        entry = installed[0]
        pin = str(entry.get("requested_version") or pin or entry["version"])
        executable = os.path.join(str(entry["install_path"]), "bin", tool)
        if not (os.path.isfile(executable) and os.access(executable, os.X_OK)):
            return _missing(tool, pin)
        version = str(entry["version"])
        if not _satisfies(pin, version, version):
            return _missing(tool, pin)
        adoption = _digest(
            sorted(
                [name, str(e["version"]), str(e["install_path"])]
                for name, group in listing.items()
                if name == tool
                for e in group
                if e["installed"]
            )
        )
        return Resolved(
            executable, version[:TOKEN_MAX], _digest({"pin": pin, "tool": tool}), adoption
        )
