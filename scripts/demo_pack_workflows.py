#!/usr/bin/env python3
"""End-to-end pack workflow demo — exercises all example plugins via ControlSurface.

Mirrors the agent path from docs/packs.md:
  list_plugins → run(wait_ms=0) → await_runs → query → fetch

Docker-dependent plugins skip when the daemon is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PACKS_DIR = REPO / "examples" / "packs"
PACK_TEST = REPO / "packages" / "trestle-packs" / "tests" / "test_dag.py"
YOYO_MIGRATIONS = (
    REPO / "packages" / "trestle-packs" / "tests" / "fixtures" / "yoyo-sqlite" / "migrations"
)
COMPOSE_FIXTURE = REPO / "packages" / "trestle-packs" / "tests" / "fixtures" / "minimal-compose"
COMPOSE_IMAGE = "alpine:3.20"


@dataclass
class StepResult:
    name: str
    status: str  # ok | skip | fail
    detail: str = ""


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    proc = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return proc.returncode == 0


def _docker_compose_skip_reason() -> str | None:
    if not _docker_ready():
        return "docker daemon not available"
    proc = subprocess.run(
        ["docker", "image", "inspect", COMPOSE_IMAGE],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return f"compose image not local: {COMPOSE_IMAGE} (docker pull blocked or offline)"
    return None


def _await_ok(control: Any, run_id: str, timeout_ms: int = 120_000) -> Any:
    from trestle.common.types import RunView

    views = control.await_runs([run_id], timeout_ms=timeout_ms)
    if not isinstance(views, list):
        return views
    view = views[0]
    if not isinstance(view, RunView):
        return view
    if view.state != "succeeded":
        err = control.query("last_error", {"run_id": run_id})
        msg = err if isinstance(err, dict) else str(err)
        raise RuntimeError(f"run {run_id} ended {view.state}: {msg}")
    return view


def _agent_retrieval(control: Any, run_id: str) -> dict[str, Any]:
    events = control.query("run_events", {"run_id": run_id})
    arts = control.query("run_artifacts", {"run_id": run_id})
    result = control.fetch(f"{run_id}/result", {"kind": "jsonpath", "expr": "$"})
    return {
        "event_count": len(events.get("items", [])) if isinstance(events, dict) else 0,
        "artifacts": [a.get("name") for a in arts.get("items", []) if isinstance(arts, dict)]
        if isinstance(arts, dict)
        else [],
        "result": result.get("values", [None])[0] if isinstance(result, dict) else result,
    }


def demo_pytest_run(control: Any) -> StepResult:
    run = control.run(
        plugin="pytest_run",
        args={"path": str(PACK_TEST), "keyword": "test_explicit_waves"},
        wait_ms=0,
    )
    view = _await_ok(control, run.run_id)
    evidence = _agent_retrieval(control, view.run_id)
    passed = evidence["result"].get("passed") if isinstance(evidence["result"], dict) else None
    if passed != 1:
        return StepResult("pytest_run", "fail", f"expected passed=1, got {evidence}")
    return StepResult("pytest_run", "ok", f"run_id={view.run_id} artifacts={evidence['artifacts']}")


def demo_migrate_apply(control: Any, workdir: Path) -> StepResult:
    db_path = workdir / "migrate.db"
    mig_dir = workdir / "migrations"
    mig_dir.mkdir()
    for path in YOYO_MIGRATIONS.glob("*.sql"):
        mig_dir.joinpath(path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    run = control.run(
        plugin="migrate_apply",
        args={
            "backend": "yoyo",
            "database_url": f"sqlite:///{db_path}",
            "migrations_dir": str(mig_dir),
        },
        wait_ms=0,
    )
    view = _await_ok(control, run.run_id)
    evidence = _agent_retrieval(control, view.run_id)
    applied = (
        evidence["result"].get("applied_count") if isinstance(evidence["result"], dict) else None
    )
    if applied != 1:
        return StepResult("migrate_apply", "fail", f"expected applied_count=1, got {evidence}")
    return StepResult("migrate_apply", "ok", f"run_id={view.run_id} applied={applied}")


def demo_docker_stack(control: Any, workdir: Path) -> StepResult:
    skip = _docker_compose_skip_reason()
    if skip:
        return StepResult("docker_stack", "skip", skip)

    project_dir = workdir / "compose-project"
    project_dir.mkdir()
    compose_src = COMPOSE_FIXTURE / "docker-compose.yml"
    (project_dir / "docker-compose.yml").write_text(
        compose_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    run = control.run(
        plugin="docker_stack",
        args={
            "spec": {
                "compose_file": "docker-compose.yml",
                "project": "trestle-pack-demo",
                "teardown": "down",
                "waves": [
                    {
                        "name": "app",
                        "services": ["web"],
                        "wait": "started",
                        "timeout_s": 60,
                    }
                ],
            },
            "workdir": str(project_dir),
        },
        wait_ms=0,
    )
    view = _await_ok(control, run.run_id, timeout_ms=180_000)
    evidence = _agent_retrieval(control, view.run_id)
    waves = (
        evidence["result"].get("waves_completed") if isinstance(evidence["result"], dict) else None
    )
    if waves != ["app"]:
        return StepResult("docker_stack", "fail", f"unexpected result: {evidence}")
    return StepResult(
        "docker_stack",
        "ok",
        f"run_id={view.run_id} artifacts={evidence['artifacts']}",
    )


def demo_integration_pipeline(control: Any, workdir: Path) -> StepResult:
    skip = _docker_compose_skip_reason()
    if skip:
        return StepResult("integration_pipeline", "skip", skip)

    project_dir = workdir / "pipeline-project"
    project_dir.mkdir()
    compose_src = COMPOSE_FIXTURE / "docker-compose.yml"
    (project_dir / "docker-compose.yml").write_text(
        compose_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    tests_dir = project_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ok.py").write_text(
        "def test_pipeline_smoke():\n    assert True\n",
        encoding="utf-8",
    )

    run = control.run(
        plugin="integration_pipeline",
        args={
            "stack_spec": {
                "compose_file": "docker-compose.yml",
                "project": "trestle-pipeline-demo",
                "teardown": "down",
                "waves": [
                    {
                        "name": "app",
                        "services": ["web"],
                        "wait": "started",
                        "timeout_s": 60,
                    }
                ],
            },
            "pytest_path": "tests",
            "workdir": str(project_dir),
        },
        wait_ms=0,
    )
    view = _await_ok(control, run.run_id, timeout_ms=300_000)
    evidence = _agent_retrieval(control, view.run_id)
    ok = evidence["result"].get("ok") if isinstance(evidence["result"], dict) else None
    if ok is not True:
        return StepResult("integration_pipeline", "fail", f"unexpected result: {evidence}")
    return StepResult(
        "integration_pipeline",
        "ok",
        f"run_id={view.run_id} artifacts={evidence['artifacts']}",
    )


def main() -> int:
    from trestle.server.main import create_kernel

    home = Path(tempfile.mkdtemp(prefix="trestle-pack-demo-"))
    workdir = Path(tempfile.mkdtemp(prefix="trestle-pack-work-"))
    kernel = create_kernel(home=home, plugin_dirs=[PACKS_DIR], skip_recovery=True)
    control = kernel.control

    catalog = control.list_plugins()
    names = sorted(row["name"] for row in catalog["items"])
    print(f"catalog: {', '.join(names)}")

    steps = [
        demo_pytest_run(control),
        demo_migrate_apply(control, workdir),
        demo_docker_stack(control, workdir),
        demo_integration_pipeline(control, workdir),
    ]

    failed = False
    for step in steps:
        prefix = {"ok": "OK", "skip": "SKIP", "fail": "FAIL"}[step.status]
        line = f"  [{prefix}] {step.name}"
        if step.detail:
            line += f" — {step.detail}"
        print(line)
        if step.status == "fail":
            failed = True

    ok_count = sum(1 for s in steps if s.status == "ok")
    skip_count = sum(1 for s in steps if s.status == "skip")
    print(f"\nPACK WORKFLOWS: {ok_count} ok, {skip_count} skipped, {len(steps)} total")

    if failed:
        print("PACK WORKFLOWS FAILED", file=sys.stderr)
        return 1

    print("PACK WORKFLOWS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
