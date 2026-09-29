"""G-E2: the only live Docker test (`test_stack_runner_live_compose`) skips
when its image is not usable, and nothing reports it. The pin proves the
skip renders UNPROVEN in the derived ledger; the target needs a host-docker
record valid for HEAD in which that node PASSED (only a preflight record
exists today)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.proof import ledger as ledger_mod
from tests.proof import results as results_mod
from tests.proof.host import record as record_mod
from tests.proof.markers import target_check

ROOT = Path(__file__).resolve().parents[3]
LIVE_FILE = "packages/trestle-packs/tests/test_docker_integration.py"
LIVE_NODE = f"{LIVE_FILE}::test_stack_runner_live_compose"
LABEL = "WR-PROOF-2:pack-docker-live"


def _run_live_node_without_docker(junit: Path) -> subprocess.CompletedProcess[str]:
    """Run the real live-compose node in a subprocess whose PATH has no
    `docker` (so the skipif holds whatever the host has cached); never
    touches an engine."""
    import os

    env = {**os.environ, "PATH": "", "PYTHONPATH": str(ROOT / "packages" / "trestle-packs")}
    env.pop("TRESTLE_PROOF_GATE", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            "pyproject.toml",
            "--rootdir",
            ".",
            f"--junitxml={junit}",
            LIVE_NODE,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _junit_outcome(junit: Path) -> str:
    case = ET.parse(junit).getroot().find(".//testcase")
    assert case is not None
    if case.find("skipped") is not None:
        return "skipped"
    if case.find("failure") is not None or case.find("error") is not None:
        return "failed"
    return "passed"


@pytest.mark.pin("G-E2")
def test_pin_skip_renders_unproven_in_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        proc = _run_live_node_without_docker(junit)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        outcome = _junit_outcome(junit)
        assert outcome == "skipped", proc.stdout

        results_dir = Path(tmp) / "results"
        results_mod.write_record(
            results_mod.Record(
                nodeid=LIVE_NODE,
                outcome=outcome,
                gate="ci-test",
                venue="CI",
                interpreter="3.12.0",
                labels=[LABEL],
            ),
            results_dir=results_dir,
        )
        report = ledger_mod.render(
            results_dir=results_dir,
            meta_config_path=results_mod.META_CONFIG_PATH,
            gates_dir=results_mod.GATES_DIR,
        )
    assert report[LABEL]["status"] == ledger_mod.UNPROVEN


@pytest.mark.target("G-E2")
@pytest.mark.proves("WR-PROOF-2", LABEL, "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, reason="defect:G-E2")
def test_target_host_docker_record_passes_live_compose() -> None:
    head = record_mod.fence_mod._git(ROOT, "rev-parse", "HEAD").stdout.strip()  # noqa: SLF001
    record = record_mod.select("host-docker", head, cwd=ROOT)
    target_check(record is not None, "G-E2", "no host-docker record admissible for HEAD")
    assert record is not None
    target_check(
        record["mode"] == "run" and record["status"] == "PASSED",
        "G-E2",
        f"host-docker record for HEAD is {record['mode']}/{record['status']}, not a passing run",
    )
    passed = [
        r
        for r in record["results"]
        if r.get("nodeid") == LIVE_NODE and r.get("outcome") == "PASSED"
    ]
    target_check(bool(passed), "G-E2", "the live compose node is not PASSED in the record")
