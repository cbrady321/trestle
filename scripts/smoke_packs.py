#!/usr/bin/env python3
"""Smoke test for example pack plugins via ControlSurface."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKS_DIR = REPO / "examples" / "packs"
PACK_TEST = REPO / "packages" / "trestle-packs" / "tests" / "test_dag.py"
YOYO_MIGRATIONS = (
    REPO / "packages" / "trestle-packs" / "tests" / "fixtures" / "yoyo-sqlite" / "migrations"
)


def _run_succeeded(control, plugin: str, args: dict, timeout_ms: int = 120_000):
    from trestle.common.types import RunView

    run = control.run(plugin=plugin, args=args, wait_ms=0)
    assert isinstance(run, RunView), run
    views = control.await_runs([run.run_id], timeout_ms=timeout_ms)
    assert isinstance(views, list), views
    view = views[0]
    assert isinstance(view, RunView), view
    if view.state != "succeeded":
        err = control.query("last_error", {"run_id": run.run_id})
        raise AssertionError((plugin, view.state, err))
    return view


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="trestle-packs-smoke-"))
    workdir = Path(tempfile.mkdtemp(prefix="trestle-packs-smoke-work-"))

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

    pytest_view = _run_succeeded(
        control,
        "pytest_run",
        {"path": str(PACK_TEST), "keyword": "test_explicit_waves"},
    )
    fetched = control.fetch(
        f"{pytest_view.run_id}/result", {"kind": "jsonpath", "expr": "$.passed"}
    )
    assert isinstance(fetched, dict), fetched
    assert fetched.get("values") == [1], fetched

    mig_dir = workdir / "migrations"
    mig_dir.mkdir()
    for path in YOYO_MIGRATIONS.glob("*.sql"):
        mig_dir.joinpath(path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    db_path = workdir / "smoke.db"
    migrate_view = _run_succeeded(
        control,
        "migrate_apply",
        {
            "backend": "yoyo",
            "database_url": f"sqlite:///{db_path}",
            "migrations_dir": str(mig_dir),
        },
    )
    mig_result = control.fetch(
        f"{migrate_view.run_id}/result",
        {"kind": "jsonpath", "expr": "$.applied_count"},
    )
    assert isinstance(mig_result, dict), mig_result
    assert mig_result.get("values") == [1], mig_result

    print(
        "PACKS SMOKE OK "
        f"pytest={pytest_view.run_id} migrate={migrate_view.run_id} "
        f"passed={fetched.get('values')} applied={mig_result.get('values')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
