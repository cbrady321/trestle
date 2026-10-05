#!/usr/bin/env python3
"""Stand-in for `mise` (MC-B-06, L.RB-4.1): the JSON subset the toolchain resolver reads.

Invoked only by absolute path, never on PATH and never under the name `mise` (MC-13). It answers
exactly one question, `ls --current --json`, in the shape upstream mise prints:
`{"<tool>": [{"version", "requested_version", "install_path", "installed", "active", "source"}]}`.
Anything else (`exec`, `run`, `x`, `install`, a shim call) exits 2 and is logged as `forbidden`,
so a resolver that shells out to them is caught (B3-C12, B3-C17 (5)).

Configuration is environment only, because the caller's argv is fixed:
  STUB_MISE_CONFIG  path of a JSON file `{"mode": ..., "tools": {"<tool>": <entry> | [<entry>]}}`
  STUB_MISE_MODE    overrides `mode`
  STUB_MISE_LOG     path; one JSON line per call `{"argv": [...], "forbidden": bool, "mode": ...}`

Modes: `normal` (tools as configured; `installed: false` entries report not installed),
`adopted-interpreter` (the `python` pin resolves to the interpreter running this stub: the clean
gate venv, DM-45), `drift` (valid JSON outside the window the resolver accepts: a list at the top
level and a non-boolean `installed`), `malformed` (not JSON at all).
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

VERSION = "2099.0.0 stub"
MODES = ("normal", "adopted-interpreter", "drift", "malformed")
LS_FLAGS = {"--current", "--json"}


def _log(argv: list[str], forbidden: bool, mode: str) -> None:
    path = os.environ.get("STUB_MISE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "forbidden": forbidden, "mode": mode}) + "\n")


def _config() -> dict[str, Any]:
    path = os.environ.get("STUB_MISE_CONFIG")
    if not path:
        return {}
    config: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return config


def _entries(value: object) -> list[dict[str, Any]]:
    return list(value) if isinstance(value, list) else [value]  # type: ignore[list-item]


def _interpreter_entry() -> dict[str, Any]:
    """The gate's interpreter as a mise install: `install_path/bin/python` is `sys.executable`
    (no symlink resolution, so the clean venv path survives)."""
    exe = Path(sys.executable)
    version = platform.python_version()
    return {
        "version": version,
        "requested_version": ".".join(version.split(".")[:2]),
        "install_path": str(exe.parent.parent),
        "source": {"type": "stub_mise.toml", "path": "adopted-interpreter"},
        "installed": True,
        "active": True,
    }


def listing(mode: str, tools: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, list[dict[str, Any]]] = {name: _entries(value) for name, value in tools.items()}
    if mode == "adopted-interpreter":
        out["python"] = [_interpreter_entry()]
    return out


def main(argv: list[str]) -> int:
    config = _config()
    mode = os.environ.get("STUB_MISE_MODE") or config.get("mode", "normal")
    if mode not in MODES:
        print(f"stub_mise: unknown mode {mode!r}", file=sys.stderr)
        return 2
    if argv == ["--version"]:
        _log(argv, False, mode)
        print(VERSION)
        return 0
    flags = [a for a in argv[1:] if a.startswith("-")]
    if argv[:1] != ["ls"] or set(flags) != LS_FLAGS:
        _log(argv, True, mode)
        print(f"stub_mise: only `ls --current --json` is stubbed, got {argv!r}", file=sys.stderr)
        return 2
    _log(argv, False, mode)
    wanted = [a for a in argv[1:] if not a.startswith("-")]
    if mode == "malformed":
        print('{"python": [{"version": "3.12", "installed": tru')
        return 0
    body = {
        k: v for k, v in listing(mode, config.get("tools", {})).items() if not wanted or k in wanted
    }
    if mode == "drift":
        flat = [dict(e, installed="yes") for entries in body.values() for e in entries]
        print(json.dumps(flat))
        return 0
    print(json.dumps(body))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
