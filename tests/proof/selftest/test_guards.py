"""Self-tests for `tests.proof.guards` (L.P0-0b.5; MC-13 guards I,
MC-P0-09)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.proof import guards

ROOT = Path(__file__).resolve().parents[3]


def _plant_binary(directory: Path, name: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_planted_aws_on_path_fails_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _plant_binary(tmp_path, "aws")
        fake_path = os.pathsep.join([str(tmp_path), os.environ.get("PATH", "")])

        script = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); from tests.proof import guards; "
            "guards.check_forbidden_path_binaries()"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PATH": fake_path},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 3
        assert "aws" in proc.stderr


def test_clean_path_hides_forbidden_keeps_others() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _plant_binary(tmp_path, "aws")
        _plant_binary(tmp_path, "git")  # a decoy real tool alongside a forbidden one
        fake_path = os.pathsep.join([str(tmp_path), os.environ.get("PATH", "")])

        shadow = guards.build_clean_path(fake_path)
        assert shutil.which("aws", path=shadow) is None
        assert shutil.which("gradle", path=shadow) is None
        assert shutil.which("git", path=shadow) is not None
        assert shutil.which("python3", path=shadow) is not None


def test_clean_path_github_env_appends_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _plant_binary(tmp_path, "gradle")
        fake_path = os.pathsep.join([str(tmp_path), os.environ.get("PATH", "")])
        github_env = tmp_path / "github_env"
        github_env.write_text("", encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, "-m", "tests.proof.guards", "clean-path", "--github-env"],
            cwd=ROOT,
            env={**os.environ, "PATH": fake_path, "GITHUB_ENV": str(github_env)},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        lines = [line for line in github_env.read_text(encoding="utf-8").splitlines() if line]
        assert len(lines) == 1
        assert lines[0].startswith("PATH=")
        shadow = lines[0][len("PATH=") :]
        assert shutil.which("gradle", path=shadow) is None


def test_no_model_client_in_deps_or_imports() -> None:
    assert guards.scan_for_model_client_imports() == []
