"""Self-tests for plugin loading across the root and packs sessions
(L.P0-0a.2, CSC-12), and the import-origin guard (L.P0-0b.5)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_plugin_loaded_in_root_and_packs_sessions() -> None:
    root_proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--markers"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert "@pytest.mark.proves" in root_proc.stdout

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
            "--markers",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert "@pytest.mark.proves" in packs_proc.stdout


def test_bare_packs_invocation_documented_as_unguarded() -> None:
    """CSC-12: a bare `pytest packages/trestle-packs/tests` skips the root
    conftest (its own pyproject.toml becomes rootdir), so the plugin never
    loads there. This is documented at the call site, not silently relied
    on."""
    conftest_text = (ROOT / "conftest.py").read_text()
    assert "packs-only session" in conftest_text
    assert "-c pyproject.toml --rootdir ." in conftest_text


def test_import_origin_guard_rejects_stale_packs_copy() -> None:
    """A `trestle_packs` copy that resolves outside the rootdir and
    diverges from `packages/trestle-packs/trestle_packs` must fail the
    guard — the same shape as the stale miniforge site-packages copy this
    host carries when `PYTHONPATH` is not set."""
    with tempfile.TemporaryDirectory() as tmp:
        stale_root = Path(tmp) / "stale"
        stale_pkg = stale_root / "trestle_packs"
        stale_pkg.mkdir(parents=True)
        (stale_pkg / "__init__.py").write_text("PLANTED_DIVERGENT = True\n", encoding="utf-8")

        script = (
            "import sys; "
            f"sys.path.insert(0, {str(stale_root)!r}); "
            f"sys.path.insert(1, {str(ROOT)!r}); "
            "from tests.proof import guards; guards.check_import_origin()"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 3, proc.stdout + proc.stderr
        assert "trestle_packs" in proc.stderr


def test_import_origin_guard_accepts_canonical_packs_copy() -> None:
    """The counterpart of the rejection test: with the real
    `packages/trestle-packs` on `PYTHONPATH`, the guard passes."""
    proc = subprocess.run(
        [sys.executable, "-c", "from tests.proof import guards; guards.check_import_origin()"],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "packages" / "trestle-packs"),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_guard_env_applies_in_packs_session() -> None:
    """L.P0-0b.6: the packs test session still passes when run under
    `guards.scrub_subprocess_env()` — the scrub removes credential-bearing
    variables without breaking anything the packs suite actually needs."""
    from tests.proof import guards

    base_env = {**os.environ, "PYTHONPATH": str(ROOT / "packages" / "trestle-packs")}
    scrubbed_env = guards.scrub_subprocess_env(base_env)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            "pyproject.toml",
            "--rootdir",
            ".",
            "packages/trestle-packs/tests",
        ],
        cwd=ROOT,
        env=scrubbed_env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_every_ci_pytest_job_has_clean_path_step() -> None:
    """Static check over `.github/workflows/ci.yml`: every job with a
    `pytest` step has a `guards clean-path` step earlier in that same
    job."""
    lines = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").splitlines()

    jobs: list[tuple[str, list[str]]] = []
    current_job: str | None = None
    current_lines: list[str] = []
    in_jobs_block = False
    for line in lines:
        if line.rstrip() == "jobs:":
            in_jobs_block = True
            continue
        if not in_jobs_block:
            continue
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            if current_job is not None:
                jobs.append((current_job, current_lines))
            current_job = line.strip().rstrip(":")
            current_lines = []
            continue
        if current_job is not None:
            current_lines.append(line)
    if current_job is not None:
        jobs.append((current_job, current_lines))

    assert jobs, "no jobs parsed from ci.yml"

    for job_name, job_lines in jobs:
        run_lines = [line for line in job_lines if "run:" in line]
        pytest_indices = [
            i for i, line in enumerate(run_lines) if "pytest" in line and "run:" in line
        ]
        if not pytest_indices:
            continue
        clean_path_indices = [
            i for i, line in enumerate(run_lines) if "guards" in line and "clean-path" in line
        ]
        assert clean_path_indices, f"job {job_name!r} runs pytest with no clean-path step"
        for pytest_index in pytest_indices:
            assert any(cp < pytest_index for cp in clean_path_indices), (
                f"job {job_name!r} runs pytest before its clean-path step"
            )
