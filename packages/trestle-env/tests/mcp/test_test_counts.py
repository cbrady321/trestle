"""A failing system test is decisive in the answer: counts and ids (L.RB-5.2; B4.3, WR-ENV-5).

The operator's catalog lists the test `system`: the `run` task of `system-tests`, which pins Python
and runs pytest over `suite/` (the ERROR-ONLY suite of `tests/fixtures/system-tests/`: every test
errors at setup, none fails, none passes). The published `reference_env` runs on the twins' fake
engine, the REAL toolchain resolver over the mise-shaped stub, and the real pytest JUnit runner
executing the real interpreter. A green exit status, or `failed == 0`, would mislead: the answer
must be `failed`, carry `test_counts.errors > 0`, and name the failing ids behind its `detail`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from twin import harness
from twin.toolchain_world import World, call

SUITE = Path(__file__).resolve().parents[1] / "fixtures" / "system-tests" / "suite"
TEST_ID = "system"
NODE = f"test.{TEST_ID}"


@pytest.mark.proves("WR-ENV-5", "B4.3", "B", "B", "MCP+PROC", "CI")
@pytest.mark.proves("WR-ENV-5", "WR-ENV-5:failing-answer-counts-ids", "B", "B", "MCP+PROC", "CI")
def test_error_only_suite_never_passed_counts_errors(tmp_path: Path) -> None:
    world = World(
        tmp_path / "world-dir",
        installed=True,
        tests=[{"id": TEST_ID, "project": "system-tests", "task": "run"}],
        runs="interpreter",
    )
    shutil.copytree(SUITE, world.projects / "system-tests" / "suite")
    with harness.reference_host(tmp_path / "home", world.environ(tmp_path / "engine.json")) as host:
        answer = call(host, TEST_ID, env="system-tests")
        run_id = answer["run_id"]
        events = harness.events(harness.run_dir(host, run_id))
    body = answer["answer"]
    assert body["outcome"] == "failed", body  # an error-only suite never passes
    assert body["primary"]["path"] == [NODE]
    counts = body["test_counts"]
    assert counts["errors"] > 0 and counts["passed"] == 0 and counts["failed"] == 0, counts
    # the failing ids are behind `detail` (the full answer) and named in the run's evidence
    assert body["detail"] == f"{run_id}/answer"
    failing = [e["payload"] for e in events if e.get("kind") == "test.failing"]
    assert len(failing) == 1 and failing[0]["test"] == TEST_ID, events
    assert failing[0]["failing"] and any("test_system" in i for i in failing[0]["failing"])
