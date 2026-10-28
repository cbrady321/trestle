"""The RB-8.2 proof nodes' one call to the `local_override` test plugin (L.RB-8.2; MC-12, MC-19).

Shared by the local-override HOST nodes and their twins, like `twin.variants` for the RB-12 nodes:
the plugin (`fixtures/local_override.py`) is published into the MCP host's plugin directory, the
HOST node binds the real container adapter, the twin sets the `fake_binding` seam; the local
process is the REAL local process port in both (an override never touches the engine), and the
operator's toolchain is the mise-shaped stub in adopted-interpreter mode (the clean gate venv): the
call and the record reads are the same.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import mcp_host, tolerances

from trestle_env.plugins import _local
from twin import harness

REPO = Path(__file__).resolve().parents[4]
PLUGIN = Path(__file__).resolve().parents[1] / "fixtures" / "local_override.py"
PLUGIN_NAME = "local_override"
STUB_MISE = REPO / "tests" / "fixtures" / "stubs" / "stub_mise.py"
OVERRIDE = "http_support_local"
LOGICAL = "http_support"
PROJECT = "override-app"
LAUNCH_EVENT = "override.launch"


def operator_environ(tmp: Path, repository: Path | None = REPO) -> dict[str, str | None]:
    """The operator's environment for a local override: the mise-shaped stub in adopted-interpreter
    mode (its `python` pin resolves to this venv's interpreter) and the project's directory
    (`repository`; a directory that does not exist is the missing-repository case)."""
    tmp.mkdir(parents=True, exist_ok=True)
    config = tmp / "mise.json"
    config.write_text(json.dumps({"mode": "adopted-interpreter", "tools": {}}), encoding="utf-8")
    wrapper = tmp / "stub_mise"
    wrapper.write_text(
        f"#!{sys.executable}\nimport runpy, sys\n"
        f"sys.argv = ['stub_mise.py', *sys.argv[1:]]\n"
        f"runpy.run_path({str(STUB_MISE)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    directory = str(repository if repository is not None else tmp / "no-such-repository")
    return {
        _local.MISE_PATH_ENV: str(wrapper),
        _local.TOOLCHAIN_ENV_ENV: json.dumps({"STUB_MISE_CONFIG": str(config)}),
        _local.PROJECT_DIRS_ENV: json.dumps({PROJECT: directory}),
    }


@contextmanager
def overrides_host(
    home: Path, environ: Mapping[str, str | None] | None = None
) -> Iterator[mcp_host.McpHost]:
    """The MCP host with `local_override` published; `environ` set for it and its processes."""
    changes: dict[str, str | None] = {"PYTHONPATH": harness.plugin_pythonpath(), **(environ or {})}
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (home / "plugins").mkdir(parents=True, exist_ok=True)
        (home / "plugins" / PLUGIN.name).write_bytes(PLUGIN.read_bytes())
        with mcp_host.McpHost(home=home, timeout_s=harness.HOST_TIMEOUT_S) as host:
            yield host
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_terminal(
    host: mcp_host.McpHost,
    env: str,
    services: list[str] | None = None,
    overrides: list[str] | None = None,
) -> dict[str, Any]:
    """THE one call; MC-12 counts it once."""
    sent = host.request_count()
    args: dict[str, Any] = {"env": env}
    if services is not None:
        args["services"] = services
    if overrides is not None:
        args["overrides"] = overrides
    answer = host.call(
        "run",
        {
            "plugin": PLUGIN_NAME,
            "args": args,
            "wait_ms": tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert host.request_count() == sent + 1, "one request, counted once by the MCP host"
    assert isinstance(answer, dict), answer
    harness.note_passed(answer)
    return answer


def launch_events(run_dir: Path) -> list[dict[str, Any]]:
    """The `override.launch` evidence events of a run: the command the override was launched by."""
    return [e for e in harness.events(run_dir) if LAUNCH_EVENT in json.dumps(e)]


def unredacted(path: str) -> str:
    """An evidence path with the redaction of the user's home directory undone."""
    return path.replace("<user-home>", str(Path.home()))


def override_state(answer: Mapping[str, Any]) -> dict[str, Any]:
    """The override node's row in the answer's listing (path `http_support/http_support_local`)."""
    (node,) = [n for n in answer["answer"]["listed"] if n["path"] == [LOGICAL, OVERRIDE]]
    assert isinstance(node, dict)
    return node
