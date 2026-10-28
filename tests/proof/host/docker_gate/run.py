"""`python -m tests.proof.host.docker_gate run [--select ID …] [-- <pytest args>]` and `pin-images`
(CSC-5; L.NW-2.10; MC-B-02 run half, MC-B-10).

`run` — one invocation, one record (one run per sha): refuse a sha that already has a host-docker
record; take the CSC-12 host lock only when `TRESTLE_HOST_LOCK_HELD` is unset; run the strict
preflight INSIDE that hold (it re-acquires nothing); snapshot the inventory; run pytest over the
root session's testpaths with `TRESTLE_HOST_GATE=docker`, `TRESTLE_DOCKER_ENDPOINT` (the
`desktop-linux` endpoint, read once by the preflight) and `TRESTLE_IMAGE_<ROLE>=<repo>@<digest>`
exported from images.toml; snapshot again; CSC-10 housekeeping (run-scoped and fixture-labelled
containers and networks, fixture-labelled volumes only, never an unlabelled volume; only objects
the run added or changed, recorded as residue); write the record. The whole run is bounded by
`HOST_RUN_MAX`: a run past it is killed with its process group, the lock is freed, "HOST run timed
out" is reported and no record is written. Nothing is pulled, started, stopped or reconfigured.

`pin-images` — records, with `docker image inspect` only (plus the read-only `context inspect` for
the endpoint), each role's registry digest: the `RepoDigests` entry whose repository equals the
ref's (never the image `Id`). A role whose ref is not cached, or whose cached image has no such
entry, keeps its digest, so an unpinned role stays `""` and the strict preflight names it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from tests.proof import fence as fence_mod
from tests.proof.host import host_lock
from tests.proof.host.docker_gate import inventory, preflight, select

ROOT = Path(__file__).resolve().parents[4]
RECORD_DIR = ROOT / "tests" / "proof" / "host" / "host-docker"

PIN_UNMET_EXIT = 3
RUN_REFUSED_EXIT = 2

_OUTCOME = {
    "passed": "PASSED",
    "failed": "FAILED",
    "skipped": "SKIPPED",
    "xfailed": "XFAIL",
    "xpassed": "XPASS",
}


def image_env(images: dict[str, dict[str, str]]) -> dict[str, str]:
    """`TRESTLE_IMAGE_<ROLE>=<repo>@<digest>` for every pinned role (MC-B-10)."""
    out = {}
    for role, spec in images.items():
        if spec.get("digest"):
            repo = preflight.repo_of(spec["ref"])
            out[f"TRESTLE_IMAGE_{role.upper()}"] = f"{repo}@{spec['digest']}"
    return out


def split_marker_args(pytest_args: list[str]) -> tuple[str | None, list[str]]:
    """Pull `-m EXPR` out of the `--` args: it goes to `select.py`, never to pytest's own `-m`."""
    expr: str | None = None
    rest: list[str] = []
    args = list(pytest_args)
    while args:
        arg = args.pop(0)
        if arg in ("-m", "--markexpr") and args:
            expr = _or(expr, args.pop(0))
        elif arg.startswith("--markexpr="):
            expr = _or(expr, arg.split("=", 1)[1])
        elif arg.startswith("-m") and len(arg) > 2 and not arg.startswith("--"):
            expr = _or(expr, arg[2:])
        else:
            rest.append(arg)
    return expr, rest


def _or(current: str | None, new: str) -> str:
    return new if current is None else f"({current}) or ({new})"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _kill_group(child: subprocess.Popen[str]) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _housekeeping(
    docker_bin: str,
    endpoint: str,
    after: dict,
    residue: list[dict],
    runner,
) -> list[dict]:
    """CSC-10: remove what the run added or changed and can be attributed to it (a run-scoped
    selector or the fixture label): containers and networks; volumes only when fixture-LABELLED
    (an unlabelled volume is never touched, a selector-named volume is residue, not removed).
    Returns each residue row with `removed`."""
    labelled_volumes = {
        v["name"]
        for v in after.get("volumes", [])
        if v.get("labels", {}).get(inventory.FIXTURE_LABEL)
    }
    out = []
    for entry in residue:
        row = dict(entry)
        argv: list[str] | None = None
        if entry["change"] == "removed":
            argv = None  # already gone
        elif entry["kind"] == "containers":
            argv = ["rm", "-f", str(entry["id"])]
        elif entry["kind"] == "networks":
            argv = ["network", "rm", str(entry["id"])]
        elif entry["kind"] == "volumes" and entry["name"] in labelled_volumes:
            argv = ["volume", "rm", str(entry["name"])]
        if argv is None:
            row["removed"] = False
        else:
            done = runner(inventory.docker_cmd(docker_bin, endpoint, *argv), inventory.docker_env())
            row["removed"] = done.returncode == 0
        out.append(row)
    return out


def run(
    select_ids: list[str] | None = None,
    pytest_args: list[str] | None = None,
    cwd: Path | None = None,
    record_dir: Path | None = None,
    *,
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
    pytest_runner=None,
    command: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> int:
    cwd = cwd or ROOT
    record_dir = record_dir or RECORD_DIR
    sha = preflight._current_sha(cwd)  # noqa: SLF001
    record_path = record_dir / f"{sha}.json"
    if record_path.exists():
        print(f"docker_gate run: a record for {sha} already exists; refusing (one run per sha)")
        return RUN_REFUSED_EXIT

    real_interpreter = pip_runner is None or (pytest_runner is None and command is None)
    if real_interpreter and not host_lock.venv_python(venv).exists():
        print(f"docker_gate run: clean venv missing: {host_lock.venv_python(venv)}")
        record = _record(sha, "PRECONDITION_UNMET", "unavailable", "unavailable")
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, indent=2))
        return 1

    bound = fence_mod.HOST_RUN_MAX if host_run_max is None else host_run_max
    docker_run = runner or preflight.bounded_runner(bound, clock)
    run_pytest = pytest_runner or _bounded_pytest(cwd, bound, clock)
    docker_bin = docker_path if docker_path is not None else preflight.resolve_docker(which)
    images = images if images is not None else preflight.load_images()
    expression, rest_args = split_marker_args(list(pytest_args or []))
    audit_dir = tempfile.mkdtemp(prefix="docker_gate_")
    audit_run = Path(audit_dir) / "run.json"
    facts: dict = {}
    proc = None
    try:
        with host_lock.hold(worktree=cwd, pip_runner=pip_runner, venv=venv, lock_path=lock_path):
            rc = preflight.strict_preflight(
                cwd=cwd,
                record_dir=record_dir,
                docker_path=docker_bin,
                runner=docker_run,
                images=images,
                pip_runner=pip_runner,
                venv=venv,
                info_runner=info_runner,
                lock_path=lock_path,
                facts=facts,
            )
            if rc != 0:
                print("docker_gate run: strict preflight unmet; not running (record written)")
                return rc
            endpoint = facts["endpoint"]
            docker_bin = facts["cli_path"]
            before = inventory.snapshot(docker_bin, endpoint, runner=docker_run)

            env = dict(os.environ)
            env.update(extra_env or {})
            env.pop("DOCKER_HOST", None)
            env.pop("DOCKER_CONTEXT", None)
            env.update(image_env(images))
            env.update(
                {
                    "TRESTLE_HOST_GATE": "docker",
                    "TRESTLE_PROOF_GATE": "host-docker",
                    "TRESTLE_DOCKER_ENDPOINT": endpoint,
                    "TRESTLE_AUDIT_OUT": str(audit_run),
                    select.SELECT_ENV: json.dumps(list(select_ids or [])),
                }
            )
            env.pop(select.MARKEXPR_ENV, None)
            if expression:
                env[select.MARKEXPR_ENV] = expression

            argv = command
            if argv is None:
                argv = [
                    *host_lock.venv_command(venv),
                    "-m",
                    "pytest",
                    "-q",
                    "-c",
                    "pyproject.toml",
                    "--rootdir",
                    ".",
                    "-p",
                    "tests.proof.audit_plugin",
                    "-p",
                    "tests.proof.host.docker_gate.select",
                    *rest_args,
                ]
            proc = run_pytest(argv, env)

            after = inventory.snapshot(docker_bin, endpoint, runner=docker_run)
            diff = inventory.compute_diff(before, after)
            residue = (
                _housekeeping(docker_bin, endpoint, after, diff["residue"], docker_run)
                if after["engine"]["reachable"]
                else diff["residue"]
            )
        audit = _read_json(audit_run)
    finally:
        shutil.rmtree(audit_dir, ignore_errors=True)

    results = []
    if audit is not None:
        labels_of = {n["nodeid"]: n.get("labels", []) for n in audit.get("nodes", [])}
        results = [
            {
                "nodeid": nid,
                "outcome": _OUTCOME.get(o.get("outcome"), "ERROR"),
                "labels": labels_of.get(nid, []),
            }
            for nid, o in audit.get("outcomes", {}).items()
        ]
    if not results and proc.returncode == 0:
        print("docker_gate run: the run set is empty and nothing ran")
        status = "PRECONDITION_UNMET"
    elif proc.returncode != 0 or inventory.diff_failed(diff):
        status = "FAILED"
    else:
        status = "PASSED"
    py_version, py_platform = host_lock.venv_interpreter_info(venv, runner=info_runner)
    record = _record(sha, status, py_version, py_platform)
    record.update(
        {
            "results": results,
            "engine": {
                "cli_path": docker_bin,
                "endpoint": endpoint,
                "server_version": before["engine"].get("server_version"),
                "reachable_before": bool(before["engine"]["reachable"]),
                "reachable_after": bool(after["engine"]["reachable"]),
            },
            "images": {
                role: f"{preflight.repo_of(spec['ref'])}@{spec['digest']}"
                for role, spec in images.items()
                if spec.get("digest")
            },
            "inventory_before": before,
            "inventory_after": after,
            "diff": {
                "unattributed": diff["unattributed"],
                "engine_state_changed": diff["engine_state_changed"],
            },
            "residue": residue,
        }
    )
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2))
    print(f"docker_gate run: wrote {record_path} (status {status})")
    return 0


def _record(sha: str, status: str, py_version: str, py_platform: str) -> dict:
    return {
        "schema": 1,
        "gate": "host-docker",
        "sha": sha,
        "mode": "run",
        "python": py_version,
        "platform": py_platform,
        "results": [],
        "status": status,
    }


def _bounded_pytest(cwd: Path, bound: float, clock):
    """Run pytest in its own process group under whatever remains of the run's `HOST_RUN_MAX`
    (created beside the docker runner, so both share one start time)."""
    now = clock or time.monotonic
    start = now()

    def run_pytest(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        remaining = bound - (now() - start)
        if remaining <= 0:
            raise host_lock.HostRunTimedOut("HOST run timed out")
        child = subprocess.Popen(  # noqa: S603
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
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

    return run_pytest


# -- pin-images ----------------------------------------------------------------------------------


def format_images(header: str, images: dict[str, dict[str, str]]) -> str:
    body = "".join(
        f'\n[{role}]\nref = "{spec["ref"]}"\ndigest = "{spec.get("digest", "")}"\n'
        for role, spec in images.items()
    )
    return header + body


def _header(path: Path) -> str:
    lines = []
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def pin_images(
    images_path: Path | None = None,
    *,
    docker_path: str | None = None,
    runner=None,
    which=None,
    lock_path: Path | None = None,
    host_run_max: float | None = None,
    clock: Callable[[], float] | None = None,
) -> int:
    """Record each role's `RepoDigests` digest in images.toml (exit 0 when every role is pinned,
    `PIN_UNMET_EXIT` when one stays unpinned, its reason printed). `docker image inspect` only."""
    images_path = images_path or preflight.IMAGES_PATH
    images = preflight.load_images(images_path)
    docker_run = runner or preflight.bounded_runner(host_run_max, clock)
    docker_bin = docker_path if docker_path is not None else preflight.resolve_docker(which)
    if not docker_bin:
        print("docker_gate pin-images: docker CLI not resolved")
        return PIN_UNMET_EXIT
    docker_bin = os.path.abspath(docker_bin)
    unpinned = 0
    with host_lock.hold(lock_path=lock_path, repoint=False):
        endpoint = preflight.read_endpoint(docker_bin, runner=docker_run)
        if endpoint is None:
            print("docker_gate pin-images: endpoint of context desktop-linux unreadable")
            return PIN_UNMET_EXIT
        environ = inventory.docker_env()
        for role, spec in images.items():
            done = docker_run(
                inventory.docker_cmd(docker_bin, endpoint, "image", "inspect", spec["ref"]),
                environ,
            )
            if done.returncode != 0:
                print(f"docker_gate pin-images: {role}: {spec['ref']} not cached (left as is)")
                unpinned += 0 if spec.get("digest") else 1
                continue
            digest = preflight.repo_digest_of(spec["ref"], done.stdout)
            if digest is None:
                print(
                    f"docker_gate pin-images: {role}: {spec['ref']} has no RepoDigest (left as is)"
                )
                unpinned += 0 if spec.get("digest") else 1
                continue
            spec["digest"] = digest
            print(f"docker_gate pin-images: {role}: {spec['ref']} -> {digest}")
    images_path.write_text(format_images(_header(images_path), images))
    return PIN_UNMET_EXIT if unpinned else 0
