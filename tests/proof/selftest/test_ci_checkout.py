"""Self-tests for `.github/workflows/ci.yml` (L.P0-0a.6).

These are static checks only: the workflow itself cannot run in this
delivery (no push, no PR, no GitHub Actions run). `test_full_history_checkout`
confirms the `test` and `proof-ledger` jobs check out with `fetch-depth: 0`
and `fetch-tags: true`, so CM-6's ancestry reads and `git diff <sha>..HEAD`
see full history. The uploaded-ledger shape assertion (18 cells, 65 clauses,
69 parts) is a later-phase claim once the matrix switches on at L.P0-0c.1;
out of scope here (a `pending-CI` item, not a gap in this leaf)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CI_YML_PATH = ROOT / ".github" / "workflows" / "ci.yml"


def _load_ci_yml() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI_YML_PATH.read_text())


@pytest.mark.parametrize("job_name", ["test", "proof-ledger", "ckpt"])
def test_full_history_checkout(job_name: str) -> None:
    workflow = _load_ci_yml()
    job = workflow["jobs"][job_name]
    checkout_step = next(
        s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout")
    )
    assert checkout_step["with"]["fetch-depth"] == 0
    assert checkout_step["with"]["fetch-tags"] is True


def test_ci_yml_parses_and_has_expected_jobs() -> None:
    workflow = _load_ci_yml()
    assert set(workflow["jobs"]) == {
        "lint",
        "test",
        "compat",
        "guards",
        "ancestry",
        "d2-straddle",
        "proof-ledger",
        "ckpt",
    }


def test_lint_job_runs_mypy_ratchet() -> None:
    workflow = _load_ci_yml()
    lint_runs = [s.get("run", "") for s in workflow["jobs"]["lint"]["steps"]]
    assert any("mypy-ratchet --max 1" in r for r in lint_runs)


def test_test_and_compat_jobs_set_proof_gate_env() -> None:
    workflow = _load_ci_yml()
    test_envs = [s.get("env", {}) for s in workflow["jobs"]["test"]["steps"]]
    assert any(e.get("TRESTLE_PROOF_GATE") == "ci-test" for e in test_envs)
    compat_envs = [s.get("env", {}) for s in workflow["jobs"]["compat"]["steps"]]
    assert any(e.get("TRESTLE_PROOF_GATE") == "ci-compat" for e in compat_envs)


def test_proof_ledger_needs_test_and_compat() -> None:
    workflow = _load_ci_yml()
    assert set(workflow["jobs"]["proof-ledger"]["needs"]) == {
        "test",
        "compat",
        "guards",
        "ancestry",
        "d2-straddle",
    }


def test_yaml_only_lint_check_via_subprocess() -> None:
    """Belt-and-suspenders: confirm the file parses via a plain subprocess
    `python -c` call too, independent of pytest's own import machinery."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import yaml, sys; yaml.safe_load(open(sys.argv[1])); print('ok')",
            str(CI_YML_PATH),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "ok"
