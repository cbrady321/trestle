"""Self-tests for `tests.proof.guards` (L.P0-0b.5 guards I, L.P0-0b.6
guards II; MC-13, MC-P0-09)."""

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


def test_planted_credential_open_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fake_home = Path(tmp)
        aws_dir = fake_home / ".aws"
        aws_dir.mkdir()
        cred_path = aws_dir / "credentials"
        cred_path.write_text("[default]\naws_access_key_id = planted\n", encoding="utf-8")

        script = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); from tests.proof import guards; "
            "guards.install_credential_audit_hook(); "
            f"open({str(cred_path)!r})"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "HOME": str(fake_home)},
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert "credential-read guard" in proc.stderr


def test_subprocess_env_scrubbed() -> None:
    base = {
        "AWS_ACCESS_KEY_ID": "planted-key",
        "AWS_SECRET_ACCESS_KEY": "planted-secret",
        "AWS_PROFILE": "planted-profile",
        "OTHER": "kept",
    }
    scrubbed = guards.scrub_subprocess_env(base)
    assert scrubbed["AWS_ACCESS_KEY_ID"] == guards.ENV_SENTINEL
    assert scrubbed["AWS_SECRET_ACCESS_KEY"] == guards.ENV_SENTINEL
    assert scrubbed["AWS_PROFILE"] == guards.ENV_SENTINEL
    assert scrubbed["AWS_SHARED_CREDENTIALS_FILE"] == guards.ENV_SENTINEL
    assert scrubbed["AWS_CONFIG_FILE"] == guards.ENV_SENTINEL
    assert scrubbed["OTHER"] == "kept"
    docker_config = Path(scrubbed["DOCKER_CONFIG"])
    assert docker_config.is_dir()
    assert list(docker_config.iterdir()) == []


def test_docker_shim_refuses_pull_and_injects_never() -> None:
    """Runs the shim against a recorder script standing in for `docker` —
    never a real engine, and never a real pull (WR-PROOF-5:no-pull)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        recorder = tmp_path / "docker-recorder"
        calls_log = tmp_path / "calls.jsonl"
        recorder.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"with open({str(calls_log)!r}, 'a') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + chr(10))\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        recorder.chmod(0o755)

        shim_dir = guards.build_docker_shim_dir(
            real_docker=str(recorder), shim_root=tmp_path / "shim"
        )
        assert shim_dir is not None
        shim = shim_dir / "docker"

        refused = subprocess.run([str(shim), "pull", "alpine:3.20"], capture_output=True, text=True)
        assert refused.returncode != 0
        assert "refused" in refused.stderr
        assert not calls_log.exists(), (
            "the recorder (a stand-in engine) must never be invoked for a pull"
        )

        composed = subprocess.run(
            [str(shim), "compose", "-f", "docker-compose.yml", "up", "-d"],
            capture_output=True,
            text=True,
        )
        assert composed.returncode == 0, composed.stdout + composed.stderr
        recorded_calls = [
            line for line in calls_log.read_text(encoding="utf-8").splitlines() if line
        ]
        assert len(recorded_calls) == 1
        import json as _json

        recorded_argv = _json.loads(recorded_calls[0])
        assert recorded_argv == [
            "compose",
            "-f",
            "docker-compose.yml",
            "up",
            "--pull",
            "never",
            "-d",
        ]


def test_build_docker_shim_dir_returns_none_when_docker_absent() -> None:
    assert guards.build_docker_shim_dir(path_env="") is None
