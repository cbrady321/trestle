#!/usr/bin/env python3
"""Phase C smoke — golden MCP path via ControlSurface (matches trestle serve wiring)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_SRC = REPO / "examples" / "plugins"
FIXTURE_SRC = REPO / "tests" / "fixtures" / "plugins"


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="trestle-smoke-"))
    plugins = home / "plugins"
    plugins.mkdir()
    for src_dir in (PLUGIN_SRC, FIXTURE_SRC):
        for path in src_dir.glob("*.py"):
            if not (plugins / path.name).exists():
                shutil.copy(path, plugins / path.name)

    from trestle.common.types import RequestOutcome, RunView
    from trestle.server.main import create_kernel

    kernel = create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)
    control = kernel.control

    catalog = control.list_plugins()
    names = {row["name"] for row in catalog["items"]}
    assert "echo" in names, f"echo missing from catalog: {names}"

    run = control.run(plugin="echo", args={"message": "smoke"}, wait_ms=5000)
    assert isinstance(run, RunView), run
    assert run.state == "succeeded", run.state
    run_id = run.run_id

    fetched = control.fetch(
        f"{run_id}/result",
        {"kind": "jsonpath", "expr": "$.message"},
    )
    assert isinstance(fetched, dict), fetched
    assert fetched.get("values") == ["smoke"], fetched

    last_error = control.query("last_error", {"run_id": run_id})
    assert isinstance(last_error, dict)
    assert last_error["items"] == []

    tail = control.query("run_tail", {"run_id": run_id})
    assert isinstance(tail, dict)

    refusal = control.run(plugin="no-such-plugin", args={})
    assert isinstance(refusal, RequestOutcome)
    assert refusal.origin == "admission"
    assert not hasattr(refusal, "run_id")

    path_fetch = control.fetch("/etc/passwd", {"kind": "head", "count": 1})
    assert isinstance(path_fetch, RequestOutcome)
    assert path_fetch.code.endswith("invalid_handle")

    print(f"SMOKE OK run_id={run_id} fetch={fetched.get('values')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
