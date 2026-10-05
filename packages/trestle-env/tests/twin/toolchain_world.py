"""The mise-shaped stub world the toolchain MCP cases run in (L.RB-4.5, L.RB-5.2; not a test).

`World(base, ...)` builds, on disk: an install of the pinned tool whose executable answers
`--version` (and, with `runs="interpreter"`, executes the real interpreter for anything else, so a
project's pytest suite really runs), the absolute-path stub `mise` and its JSON configuration, the
projects directory (`TRESTLE_ENV_PROJECTS_DIR`) and the operator's catalog file (the reference
catalog plus the tests the case lists). `environ(state)` is the operator environment the published
plugin runs under: the twins' fake engine plus the real toolchain resolver and task runner over the
stub (`toolchain_seam.stub_toolchain_ports`). The stub stands for mise: the real tool is
unverified (D-1, OPEN-MISE-HOST)."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

from trestle_env import schema, tree
from trestle_env.catalog import REFERENCE_PATH
from trestle_env.plugins import _bind
from twin import harness

SEAM = Path(__file__).with_name("toolchain_seam.py")
STUB_MISE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "stubs" / "stub_mise.py"
PYTHON = "3.12.4"


def script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class World:
    """Everything the stub mise and the resolver need, on disk under `base`."""

    def __init__(
        self,
        base: Path,
        *,
        installed: bool,
        tests: list[dict[str, str]] | None = None,
        runs: str = "version",
    ) -> None:
        install = base / "installs" / "python" / PYTHON
        if runs == "interpreter":  # `--version` answers itself; anything else is the interpreter
            body = (
                f"#!{sys.executable}\nimport os, sys\n"
                f"if sys.argv[1:] == ['--version']:\n    print('Python {PYTHON}')\n"
                "else:\n    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
            )
        else:
            body = (
                f"#!{sys.executable}\nimport sys\n"
                f"print('Python {PYTHON}' if sys.argv[1:] == ['--version'] else '')\n"
            )
        script(install / "bin" / "python", body)
        self.mise = script(
            base / "bin" / "stub_mise",
            f"#!{sys.executable}\nimport runpy, sys\nsys.argv = ['stub_mise.py', *sys.argv[1:]]\n"
            f"runpy.run_path({str(STUB_MISE)!r}, run_name='__main__')\n",
        )
        self.config = base / "world" / "mise.json"
        self.log = base / "world" / "mise.log"
        self.config.parent.mkdir(parents=True)
        entry = {
            "version": PYTHON,
            "requested_version": "3.12",
            "install_path": str(install),
            "installed": installed,
            "active": True,
            "source": {"type": "stub_mise.toml", "path": "world"},
        }
        self.config.write_text(json.dumps({"mode": "normal", "tools": {"python": entry}}), "utf-8")
        self.projects = base / "projects"
        for project in ("demo-py", "system-tests"):
            (self.projects / project).mkdir(parents=True)
        self.artifacts = base / "artifacts"
        self.catalog = base / "catalog.json"
        data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
        data["tests"] = tests or [{"id": "demo-version", "project": "demo-py", "task": "version"}]
        self.catalog.write_text(json.dumps(data), "utf-8")

    def environ(self, state: Path) -> dict[str, str | None]:
        """The operator environment of a twin: the fake engine and the stub toolchain."""
        return {
            **harness.twin_environ(state, f"{SEAM}:stub_toolchain_ports"),
            **self.toolchain_environ(),
        }

    def toolchain_environ(self) -> dict[str, str | None]:
        """The toolchain leg's operator variables alone (catalog, projects, stub)."""
        return {
            tree.CATALOG_ENV: str(self.catalog),
            _bind.PROJECTS_DIR_ENV: str(self.projects),
            "TESTKIT_MISE": str(self.mise),
            "TESTKIT_MISE_CONFIG": str(self.config),
            "TESTKIT_MISE_LOG": str(self.log),
            "TESTKIT_ARTIFACTS": str(self.artifacts),
        }

    def mise_calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]


def call(host: Any, test_id: str, env: str = "toolchain-leg") -> dict[str, Any]:
    """One `run(reference_env, tests=[test_id], completion="terminal")`."""
    answer = host.call(
        "run",
        {
            "plugin": harness.PLUGIN_NAME,
            "args": {schema.ENV_ARG: env, schema.TESTS_ARG: [test_id]},
            "wait_ms": harness.tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert isinstance(answer, dict), answer
    return answer
