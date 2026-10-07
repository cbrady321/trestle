"""`python -m tests.proof.host.docker_gate preflight [--strict]` (DM-24; L.P0-0d.6, L.NW-2.2):

Report mode (`preflight`) — CLI resolves, engine reachable on the working socket
(`~/.docker/run/docker.sock`, context `desktop-linux`; never `default`),
digest-pinned images present (else `PRECONDITION_UNMET`; never a pull).

Strict mode (`preflight --strict`, CSC-5; L.NW-2.2) — the same three checks under the CSC-12 host
lock, reaching the engine through the endpoint of the active context `desktop-linux` (read once,
`--host <endpoint>` on every argv, recorded), exit 3 with a PRECONDITION_UNMET record for the head
on any gap, exit 0 and NO record when everything holds, bounded by `HOST_RUN_MAX`. Never pulls.

Importing this module runs nothing — no docker call, no record — so
`TM-P0-13`'s probe (which imports it to read `STRICT_BUILT`) is
side-effect free.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

from tests.proof import fence as fence_mod
from tests.proof.host import host_lock
from tests.proof.host.docker_gate import inventory

ROOT = Path(__file__).resolve().parents[4]
IMAGES_PATH = Path(__file__).resolve().parent / "images.toml"
RECORD_DIR = ROOT / "tests" / "proof" / "host" / "host-docker"

DOCKER_CONTEXT = "desktop-linux"
DOCKER_SOCK = "~/.docker/run/docker.sock"

# TM-P0-13's probe reads this flag: L.NW-2.2 builds the strict mode and flips it to True, so the
# entry's probe reports `absent` from that commit on.
STRICT_BUILT = True

STRICT_UNMET_EXIT = 3


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


# -- strict mode (L.NW-2.2) ----------------------------------------------------------------------


def bounded_runner(
    bound: float | None = None, clock: Callable[[], float] | None = None
) -> Callable[[list[str], object], subprocess.CompletedProcess[str]]:
    """A docker runner whose calls share one `HOST_RUN_MAX` budget: a call still running at the
    bound has its whole process group killed and `HostRunTimedOut("HOST run timed out")` raised
    (the caller's lock context then frees the host lock; nothing is recorded)."""
    limit = fence_mod.HOST_RUN_MAX if bound is None else bound
    now = clock or time.monotonic
    start = now()

    def run(argv: list[str], env: object) -> subprocess.CompletedProcess[str]:
        remaining = limit - (now() - start)
        if remaining <= 0:
            raise host_lock.HostRunTimedOut("HOST run timed out")
        child = subprocess.Popen(  # noqa: S603
            argv,
            env=dict(env),  # type: ignore[call-overload]
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # its own process group, killed whole at the bound
        )
        try:
            out, err = child.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_group(child)
            child.communicate()
            raise host_lock.HostRunTimedOut("HOST run timed out") from None
        except BaseException:
            _kill_group(child)
            raise
        return subprocess.CompletedProcess(argv, child.returncode, out, err)

    return run


def _kill_group(child: subprocess.Popen[str]) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def read_endpoint(docker_bin: str, runner=None, env=None) -> str | None:
    """The endpoint of the active context `desktop-linux` (I-4): a read-only
    `docker context inspect`, once per invocation. `None` when it cannot be read."""
    run = runner or inventory.default_runner
    done = run(
        inventory.docker_cmd(
            docker_bin,
            None,
            "context",
            "inspect",
            DOCKER_CONTEXT,
            "--format",
            "{{.Endpoints.docker.Host}}",
        ),
        inventory.docker_env() if env is None else env,
    )
    endpoint = (done.stdout or "").strip() if done.returncode == 0 else ""
    return endpoint or None


def repo_of(ref: str) -> str:
    """`postgres:16-alpine` -> `postgres`; a registry port is not a tag."""
    name = ref.split("@", 1)[0]
    tail = name.rsplit("/", 1)[-1]
    return name[: -(len(tail) - tail.index(":"))] if ":" in tail else name


def _normal_repo(repo: str) -> str:
    for prefix in ("docker.io/", "index.docker.io/"):
        if repo.startswith(prefix):
            repo = repo[len(prefix) :]
    return repo.removeprefix("library/")


def repo_digest_of(ref: str, inspect_json: str) -> str | None:
    """The `sha256:<hex>` of the `RepoDigests` entry whose repository equals `ref`'s repository
    (never the image `Id`; `None` when the image has no such entry)."""
    try:
        data = json.loads(inspect_json)
    except ValueError:
        return None
    items = data if isinstance(data, list) else [data]
    want = _normal_repo(repo_of(ref))
    for item in items:
        for entry in item.get("RepoDigests") or []:
            repo, _, digest = str(entry).partition("@")
            if digest and _normal_repo(repo) == want:
                return digest
    return None


def _engine_facts(
    docker_bin: str | None, endpoint: str | None, version: str | None, reachable: bool
) -> dict:
    return {
        "cli_path": docker_bin or "",
        "endpoint": endpoint,
        "server_version": version,
        "reachable_before": reachable,
        "reachable_after": reachable,
    }


def strict_checks(
    docker_bin: str | None,
    images: dict[str, dict[str, str]],
    runner,
    env=None,
) -> tuple[list[str], dict]:
    """(unmet gaps, engine facts). Every gap is named: the missing CLI, the unreadable endpoint or
    unreachable engine, an unpinned role (`not cached` / `no RepoDigest` / cached but unpinned) or
    a pinned ref that `docker image inspect <repo>@<digest>` does not find. Read-only; no pull."""
    if not docker_bin:
        return ["docker CLI not resolved"], _engine_facts(None, None, None, False)
    docker_bin = os.path.abspath(docker_bin)
    endpoint = read_endpoint(docker_bin, runner=runner, env=env)
    if endpoint is None:
        return (
            [f"endpoint of context {DOCKER_CONTEXT} unreadable"],
            _engine_facts(docker_bin, None, None, False),
        )
    environ = inventory.docker_env() if env is None else env
    info = runner(
        inventory.docker_cmd(docker_bin, endpoint, "info", "--format", "{{json .}}"), environ
    )
    if info.returncode != 0:
        return (
            [f"engine unreachable on {endpoint} (context {DOCKER_CONTEXT}): {info.stderr[:200]}"],
            _engine_facts(docker_bin, endpoint, None, False),
        )
    try:
        version = json.loads(info.stdout).get("ServerVersion")
    except ValueError:
        version = None
    unmet: list[str] = []
    for role, spec in images.items():
        digest = spec.get("digest", "")
        if digest:
            ref = f"{repo_of(spec['ref'])}@{digest}"
            found = runner(
                inventory.docker_cmd(docker_bin, endpoint, "image", "inspect", ref), environ
            )
            if found.returncode != 0:
                unmet.append(f"{role}: {ref} not present")
            continue
        found = runner(
            inventory.docker_cmd(docker_bin, endpoint, "image", "inspect", spec["ref"]), environ
        )
        if found.returncode != 0:
            unmet.append(f"{role}: unpinned (not cached)")
        elif repo_digest_of(spec["ref"], found.stdout) is None:
            unmet.append(f"{role}: unpinned (no RepoDigest)")
        else:
            unmet.append(f"{role}: unpinned (cached; run pin-images)")
    return unmet, _engine_facts(docker_bin, endpoint, version, True)


def strict_preflight(
    cwd: Path | None = None,
    record_dir: Path | None = None,
    docker_path: str | None = None,
    runner=None,
    which=None,
    images: dict[str, dict[str, str]] | None = None,
    pip_runner=None,
    venv: Path | None = None,
    info_runner=None,
    lock_path: Path | None = None,
    host_run_max: float | None = None,
    clock: Callable[[], float] | None = None,
    facts: dict | None = None,
) -> int:
    """`preflight --strict`: exit 0 (everything holds, no record) or `STRICT_UNMET_EXIT` (3) with a
    PRECONDITION_UNMET record for the head. Takes the CSC-12 host lock only when
    `TRESTLE_HOST_LOCK_HELD` is unset (`host_lock.hold`); a docker call still running at
    `HOST_RUN_MAX` is killed with its process group and `HostRunTimedOut` propagates (lock freed,
    no record). When `facts` is given it is filled with the engine facts (`cli_path`, `endpoint`,
    `server_version`) so `docker_gate run` reads the endpoint once per invocation."""
    cwd = cwd or ROOT
    record_dir = record_dir or RECORD_DIR
    run = runner or bounded_runner(host_run_max, clock)
    docker_bin = docker_path if docker_path is not None else resolve_docker(which)
    images = images if images is not None else load_images()
    try:
        with host_lock.hold(worktree=cwd, pip_runner=pip_runner, venv=venv, lock_path=lock_path):
            unmet, engine = strict_checks(docker_bin, images, run)
            py_version, py_platform = host_lock.venv_interpreter_info(venv, runner=info_runner)
    except host_lock.VenvUnavailable as exc:  # MC-27: never another interpreter
        unmet, engine = [str(exc)], _engine_facts(docker_bin, None, None, False)
        py_version = py_platform = "unavailable"
    if facts is not None:
        facts.update(engine)
    if not unmet:
        return 0
    record = {
        "schema": 1,
        "gate": "host-docker",
        "sha": _current_sha(cwd),
        "mode": "preflight",
        "python": py_version,
        "platform": py_platform,
        "results": [],
        "status": "PRECONDITION_UNMET",
        "engine": {**engine, "reason": "; ".join(unmet)},
        "images": unmet,
    }
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / f"{record['sha']}.json").write_text(json.dumps(record, indent=2))
    return STRICT_UNMET_EXIT
