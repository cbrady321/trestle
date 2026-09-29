"""Self-tests for the WR-COMPAT node-id map (L.P0-0a.5)."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPAT_MAP_PATH = ROOT / "tests" / "proof" / "compat_map.toml"


def _collect_all() -> set[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "--ignore=tests/proof"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(ROOT / "packages" / "trestle-packs"),
        },
    )
    return {line.strip() for line in proc.stdout.splitlines() if "::" in line}


def test_every_mapped_nodeid_collected_and_marked() -> None:
    data = tomllib.loads(COMPAT_MAP_PATH.read_text())
    mapped_ids = {nodeid for row in data["row"] for nodeid in row["nodeids"]}
    collected = _collect_all()
    missing = mapped_ids - collected
    assert not missing, f"compat_map.toml node ids no longer collected: {sorted(missing)}"


def test_compat_marker_selects_nonzero_nodes() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "-m", "compat"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(ROOT / "packages" / "trestle-packs"),
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    collected = [line for line in proc.stdout.splitlines() if "::" in line]
    assert len(collected) >= 1


def test_alpine_skip_renders_unproven() -> None:
    from tests.proof import ledger as ledger_mod
    from tests.proof import results as results_mod

    results_dir = ROOT / "_alpine_skip_selftest_results"
    results_dir.mkdir(exist_ok=True)
    try:
        results_mod.write_record(
            results_mod.Record(
                nodeid=(
                    "packages/trestle-packs/tests/test_docker_integration.py"
                    "::test_stack_runner_live_compose"
                ),
                outcome="skipped",
                gate="ci-test",
                venue="CI",
                interpreter="3.12.9",
                labels=["WR-PROOF-2:pack-docker-live"],
            ),
            results_dir=results_dir,
        )
        report = ledger_mod.render(
            results_dir=results_dir,
            meta_config_path=results_mod.META_CONFIG_PATH,
            gates_dir=results_mod.GATES_DIR,
        )
        assert report["WR-PROOF-2:pack-docker-live"]["status"] == ledger_mod.UNPROVEN
    finally:
        for f in results_dir.glob("*.jsonl"):
            f.unlink()
        results_dir.rmdir()


def test_no_module_basename_collision_root_vs_packs() -> None:
    root_basenames = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    packs_basenames = {
        p.name for p in (ROOT / "packages" / "trestle-packs" / "tests").glob("test_*.py")
    }
    collisions = root_basenames & packs_basenames
    assert not collisions, f"module basename collision: {collisions}"
