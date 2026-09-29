"""`python -m tests.proof.host.docker_gate preflight` (DM-24; L.P0-0d.6):
report mode — CLI resolves, engine reachable on the working socket
(`~/.docker/run/docker.sock`, context `desktop-linux`; never `default`),
digest-pinned images present (else `PRECONDITION_UNMET`; never a pull).

Importing this module runs nothing — no docker call, no record — so
`TM-P0-13`'s probe (which imports it to read `STRICT_BUILT`) is
side-effect free.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

from tests.proof.host import host_lock

ROOT = Path(__file__).resolve().parents[4]
IMAGES_PATH = Path(__file__).resolve().parent / "images.toml"
RECORD_DIR = ROOT / "tests" / "proof" / "host" / "host-docker"

DOCKER_CONTEXT = "desktop-linux"
DOCKER_SOCK = "~/.docker/run/docker.sock"

# TM-P0-13: the strict preflight mode is not built here (L.NW-2.2 builds
# it and flips this to True, removing the register entry).
STRICT_BUILT = False


def _docker_host_env() -> dict[str, str]:
    import os

    sock = str(Path(DOCKER_SOCK).expanduser())
    return {**os.environ, "DOCKER_HOST": f"unix://{sock}", "DOCKER_CONTEXT": ""}


def resolve_docker(which=None) -> str | None:
    """Resolve `docker` by absolute path."""
    which = which or shutil.which
    return which("docker")


def _run(docker_bin: str, args: list[str], runner=None) -> subprocess.CompletedProcess[str]:
    runner = runner or (
        lambda a, e: subprocess.run(
            a, capture_output=True, text=True, env=e, stdin=subprocess.DEVNULL
        )
    )
    return runner([docker_bin, *args], _docker_host_env())


def load_images(path: Path | None = None) -> dict[str, dict[str, str]]:
    path = path or IMAGES_PATH
    return tomllib.loads(path.read_text())


def _current_sha(cwd: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True)
    return proc.stdout.strip()


def run_preflight(
    cwd: Path | None = None,
    docker_path: str | None = None,
    runner=None,
    which=None,
    images: dict[str, dict[str, str]] | None = None,
    venv: Path | None = None,
    info_runner=None,
) -> dict:
    """Runs the actual checks (never inside the lock — the caller wraps
    this in `host_lock.hold()`). Returns the record dict; never writes."""
    cwd = cwd or ROOT
    sha = _current_sha(cwd)
    py_version, py_platform = host_lock.venv_interpreter_info(venv, runner=info_runner)
    base = {
        "schema": 1,
        "gate": "host-docker",
        "sha": sha,
        "mode": "preflight",
        "python": py_version,
        "platform": py_platform,
        "results": [],
    }

    docker_bin = docker_path if docker_path is not None else resolve_docker(which)
    if not docker_bin:
        return {**base, "status": "PRECONDITION_UNMET", "engine": "docker CLI not resolved"}

    info = _run(docker_bin, ["info"], runner=runner)
    if info.returncode != 0:
        return {
            **base,
            "status": "PRECONDITION_UNMET",
            "engine": (
                f"engine unreachable on {DOCKER_SOCK} (context {DOCKER_CONTEXT}): "
                f"{info.stderr[:200]}"
            ),
        }

    images = images if images is not None else load_images()
    missing = []
    for name, spec in images.items():
        digest = spec.get("digest", "")
        if not digest:
            missing.append(f"{name}: unpinned (no digest cached)")
            continue
        ref = f"{spec['ref']}@{digest}"
        inspect = _run(docker_bin, ["image", "inspect", ref], runner=runner)
        if inspect.returncode != 0:
            missing.append(f"{name}: {ref} not present")

    if missing:
        return {**base, "status": "PRECONDITION_UNMET", "engine": "reachable", "images": missing}
    return {**base, "status": "PASSED", "engine": "reachable", "images": list(images)}


def preflight(
    cwd: Path | None = None,
    record_dir: Path | None = None,
    docker_path: str | None = None,
    runner=None,
    which=None,
    images: dict[str, dict[str, str]] | None = None,
    pip_runner=None,
    venv: Path | None = None,
    info_runner=None,
) -> dict:
    """Runs inside `host_lock.hold()` (CSC-12), writes the record, and
    exits 0 always (report mode)."""
    cwd = cwd or ROOT
    record_dir = record_dir or RECORD_DIR
    try:
        with host_lock.hold(worktree=cwd, pip_runner=pip_runner, venv=venv):
            record = run_preflight(
                cwd=cwd,
                docker_path=docker_path,
                runner=runner,
                which=which,
                images=images,
                venv=venv,
                info_runner=info_runner,
            )
    except host_lock.VenvUnavailable as exc:  # MC-27: never another interpreter
        record = {
            "schema": 1,
            "gate": "host-docker",
            "sha": _current_sha(cwd),
            "mode": "preflight",
            "python": "unavailable",
            "platform": "unavailable",
            "results": [],
            "status": "PRECONDITION_UNMET",
            "engine": str(exc),
        }
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / f"{record['sha']}.json").write_text(json.dumps(record, indent=2))
    return record
