#!/usr/bin/env python3
"""Smoke test for example pack plugins via ControlSurface."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKS_DIR = REPO / "examples" / "packs"
PACK_TEST = REPO / "packages" / "trestle-packs" / "tests" / "test_dag.py"


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="trestle-packs-smoke-"))

    from trestle.common.types import RunView
    from trestle.server.main import create_kernel

    kernel = create_kernel(home=home, plugin_dirs=[PACKS_DIR], skip_recovery=True)
    control = kernel.control

    catalog = control.list_plugins()
    names = {row["name"] for row in catalog["items"]}
    for plugin in ("docker_stack", "pytest_run", "migrate_apply", "integration_pipeline"):
        assert plugin in names, f"{plugin} missing from catalog: {names}"

    invalid = [row for row in catalog["items"] if not row.get("valid", True)]
    assert not invalid, f"invalid pack plugins: {invalid}"
    assert "catalog_hint" not in catalog or "trestle-packs" not in catalog.get("catalog_hint", "")

    run = control.run(
        plugin="pytest_run",
        args={"path": str(PACK_TEST), "keyword": "test_explicit_waves"},
        wait_ms=120_000,
    )
    assert isinstance(run, RunView), run
    assert run.state == "succeeded", run.state

    fetched = control.fetch(f"{run.run_id}/result", {"kind": "jsonpath", "expr": "$.passed"})
    assert isinstance(fetched, dict), fetched
    assert fetched.get("values") == [1], fetched

    print(f"PACKS SMOKE OK run_id={run.run_id} passed={fetched.get('values')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
