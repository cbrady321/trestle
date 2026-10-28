"""Self-tests for the marker vocabulary and collection-time validation in
`tests/proof/plugin.py` (L.P0-0a.2).

Planted-bad-marker cases are run as a pytest subprocess (`-p
tests.proof.plugin`, so the plugin loads without needing conftest/rootdir
discovery) against a single temp test file, so a plugin bug fails collection
in isolation rather than breaking this selftest's own collection.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NODEIDS_PATH = ROOT / "tests" / "fixtures" / "golden" / "s0" / "nodeids.txt"
# S0 ids whose test moved, old id to new. The list itself stays frozen; a move is recorded here
# with the change that made it. The stdio transport was removed (decision record
# docs/decisions/service-and-credentials.md, Decision 3, change 1a): its smoke tests run over
# streamable HTTP now, the same assertions under the HTTP smoke.
_PACKS = "packages/trestle-packs/tests/"
# the wrapper tests then moved with their wrappers into the bundled packs (delivery step 6)
_DOCKER = "packages/trestle-pack-docker/tests/"
_MIGRATE = "packages/trestle-pack-migrate/tests/"
MOVED: dict[str, str] = {
    "tests/test_mcp_stdio_smoke.py::test_stdio_serve_golden_path": (
        "tests/test_mcp_http_smoke.py::test_streamable_http_serve_golden_path"
    ),
    "tests/test_mcp_stdio_smoke.py::test_stdio_run_returns_running_on_short_wait_ms": (
        "tests/test_mcp_http_smoke.py::test_http_run_returns_running_on_short_wait_ms"
    ),
    "tests/test_mcp_stdio_smoke.py::test_stdio_empty_catalog_includes_bootstrap_fields": (
        "tests/test_mcp_http_smoke.py::test_http_empty_catalog_includes_bootstrap_fields"
    ),
    # The tool plugins were rebuilt to the bundled-pack bar (docs/decisions/
    # packs.md, delivery step 2): the python-on-whales
    # `StackRunner`, its spec and the in-process Yoyo runner are gone, and their assertions are
    # made by calling the wrappers.
    f"{_PACKS}test_docker_spec.py::test_stack_spec_from_dict": (
        f"{_DOCKER}test_docker_stack.py::test_waves_run_in_order_with_their_wait_modes"
    ),
    f"{_PACKS}test_migrate_yoyo.py::test_run_yoyo_apply_creates_table": (
        f"{_MIGRATE}test_migrate_apply.py::test_yoyo_applies_then_has_nothing_pending"
    ),
    f"{_PACKS}test_migrate_yoyo.py::test_run_yoyo_apply_idempotent_second_run": (
        f"{_MIGRATE}test_migrate_apply.py::test_yoyo_applies_then_has_nothing_pending"
    ),
    f"{_PACKS}test_migrate_yoyo.py::test_run_yoyo_apply_missing_dir": (
        f"{_MIGRATE}test_migrate_apply.py::test_yoyo_bad_input_is_an_answer"
    ),
    f"{_PACKS}test_stack_runner.py::test_stack_runner_teardown_on_failure": (
        f"{_DOCKER}test_docker_stack.py::test_failed_wave_tears_down_once_and_keeps_volumes"
    ),
    f"{_PACKS}test_stack_runner.py::test_stack_runner_teardown_stop_policy": (
        f"{_DOCKER}test_docker_stack.py::test_declared_teardown_stop_or_none[stop-exited]"
    ),
    f"{_PACKS}test_stack_runner.py::test_stack_runner_wave_sequencing": (
        f"{_DOCKER}test_docker_stack.py::test_waves_run_in_order_with_their_wait_modes"
    ),
}


def _run_planted(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    planted = tmp_path / "test_planted.py"
    planted.write_text(body)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--strict-markers",
            "-p",
            "tests.proof.plugin",
            str(planted),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_s0_nodeids_still_collected() -> None:
    ids = {MOVED.get(line, line) for line in NODEIDS_PATH.read_text().splitlines() if line.strip()}
    root_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--collect-only",
            "--ignore=tests/proof",
            "--ignore-glob=packages/trestle-packs/*",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    packs_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            "pyproject.toml",
            "--rootdir",
            ".",
            "--collect-only",
            "packages/trestle-packs/tests",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "packages" / "trestle-packs")},
    )
    collected = {
        line.strip() for line in (root_proc.stdout + packs_proc.stdout).splitlines() if "::" in line
    }
    missing = ids - collected
    assert not missing, f"S0 ids no longer collected: {sorted(missing)[:5]}"


def test_unknown_marker_is_collection_error(tmp_path: Path) -> None:
    proc = _run_planted(
        tmp_path,
        "import pytest\n\n@pytest.mark.totally_unknown_marker\ndef test_x():\n    assert True\n",
    )
    assert proc.returncode != 0
    assert "totally_unknown_marker" in (proc.stdout + proc.stderr)


def test_undeclared_label_is_collection_error(tmp_path: Path) -> None:
    proc = _run_planted(
        tmp_path,
        "import pytest\n\n"
        '@pytest.mark.proves("ROW-X", "NOT-A-DECLARED-LABEL", "core", "core", "must", "CI")\n'
        "def test_x():\n"
        "    assert True\n",
    )
    assert proc.returncode != 0
    assert "undeclared label" in (proc.stdout + proc.stderr)


def test_target_without_strict_xfail_or_gap_reason_is_error(tmp_path: Path) -> None:
    proc = _run_planted(
        tmp_path,
        'import pytest\n\n@pytest.mark.target("G-PLANTED")\ndef test_x():\n    assert True\n',
    )
    assert proc.returncode != 0
    assert "requires a strict xfail" in (proc.stdout + proc.stderr)


def test_target_with_gated_on_is_accepted(tmp_path: Path) -> None:
    proc = _run_planted(
        tmp_path,
        "import pytest\n\n"
        '@pytest.mark.target("G-PLANTED")\n'
        '@pytest.mark.gated_on("OQ-PLANTED")\n'
        "def test_x():\n"
        "    assert True\n",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_stub_proven_requires_label(tmp_path: Path) -> None:
    proc = _run_planted(
        tmp_path,
        "import pytest\n\n"
        '@pytest.mark.stub_proven("NOT-A-DECLARED-LABEL")\n'
        "def test_x():\n"
        "    assert True\n",
    )
    assert proc.returncode != 0
    assert "undeclared label" in (proc.stdout + proc.stderr)


def test_compat_label_marked_core_accepted_other_slice_mismatch_rejected(
    tmp_path: Path,
) -> None:
    accepted = _run_planted(
        tmp_path,
        "import pytest\n\n"
        '@pytest.mark.proves("R", "WR-COMPAT-12:preserved", "core", "core", "must", "CI")\n'
        "def test_x():\n"
        "    assert True\n",
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    rejected = _run_planted(
        tmp_path,
        "import pytest\n\n"
        '@pytest.mark.proves("R", "WR-COMPAT-12:preserved", "A", "core", "must", "CI")\n'
        "def test_x():\n"
        "    assert True\n",
    )
    assert rejected.returncode != 0
    assert "declared slice=compat" in (rejected.stdout + rejected.stderr)
