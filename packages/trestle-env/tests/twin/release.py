"""The release wrapper and the test operator configuration (L.RB-12.3, L.RB-12.5; V-10.1, B2-C9).

A run's container port is bound to `docker` = a shell script that execs
`tests/fixtures/stubs/docker_stop_fails.py` with its CONFIG: every docker call the run and the host
sweep make goes through it and is logged, and one release step can be made to fail. The HOST
nodes wrap the operator's docker; the twins wrap `twin/state_docker.py` over the fake engine's
state. Only the MCP host's own `config.toml` lists the wrapper in `[operator] release_executables`,
so the sweep runs the recorded `ArgvRelease` (and runs nothing when it is not listed).
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
STUB = REPO / "tests" / "fixtures" / "stubs" / "docker_stop_fails.py"
STATE_DOCKER = Path(__file__).resolve().with_name("state_docker.py")


def wrapper(directory: Path, real: list[str], fail: str | None = None) -> tuple[Path, Path]:
    """`(the docker script, its call log)`: `real` is the argv prefix of the docker it wraps."""
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "docker-calls.jsonl"
    config = directory / "docker.json"
    config.write_text(json.dumps({"real": real, "fail": fail, "log": str(log)}), encoding="utf-8")
    script = directory / "docker"
    command = " ".join(shlex.quote(part) for part in (sys.executable, str(STUB), str(config)))
    script.write_text(f'#!/bin/sh\nexec {command} "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    return script, log


def fake_real(state: Path) -> list[str]:
    """The twins' "real docker": the state CLI over the fake engine's state file."""
    return [sys.executable, str(STATE_DOCKER), str(state)]


def operator_config(home: Path, executables: list[str]) -> None:
    """The test operator configuration: `[operator] release_executables` lists the wrapper."""
    home.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(json.dumps(e) for e in executables)
    (home / "config.toml").write_text(
        f"[operator]\nrelease_executables = [{listed}]\n", encoding="utf-8"
    )


def calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def verb(args: list[str]) -> str:
    rest = args[2:] if args[:1] == ["--host"] else args
    return rest[0] if rest else ""


def release_steps(log: Path, selector: str, after: int = 0) -> list[str]:
    """The release verbs (`ps`, `stop`, `rm`) run for `selector`, from call `after` on."""
    steps = []
    for call in calls(log)[after:]:
        args = call["args"]
        named = any(selector in a.replace("\\.", ".") for a in args)  # a ps filter escapes dots
        if named and verb(args) in ("ps", "stop", "rm"):
            steps.append(verb(args) + (" (failed)" if call["failed"] else ""))
    return steps
