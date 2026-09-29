"""`python -m tests.proof.host.proc_gate run [--select <node id or -k expr> …]`
(CSC-5; L.P0-0d.3): the host-proc HOST runner. Runs inside
`host_lock.hold()` (CSC-12), sets `TRESTLE_HOST_GATE=proc` and
`TRESTLE_PROOF_GATE=host-proc`, and writes `tests/proof/host/host-proc/<sha>.json`.
One run per sha: refuses (exit 2, nothing written) when a record for
HEAD's sha already exists.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path

from tests.proof import fence as fence_mod
from tests.proof.host import host_lock

ROOT = Path(__file__).resolve().parents[3]
RECORD_DIR = ROOT / "tests" / "proof" / "host" / "host-proc"

# plan-gap: this delivery reads the default set as every collected
# `host_only`-marked node; a full "every venue-BOTH node" selection would
# need each CSC-1 label's declared venue joined back to concrete node
# ids, which no P0 leaf's label schema carries today (labels bind to a
# row/clause, not a nodeid) — recorded in the delivery return.
DEFAULT_SELECT_ARGS = ["-m", "host_only"]


def default_select_args() -> list[str]:
    return list(DEFAULT_SELECT_ARGS)


def _current_sha(cwd: Path) -> str:
    return fence_mod._git(cwd, "rev-parse", "HEAD").stdout.strip()  # noqa: SLF001


def run(
    select: list[str] | None = None,
    cwd: Path | None = None,
    record_dir: Path | None = None,
    pytest_runner=None,
    pip_runner=None,
    *,
    host_run_max: float | None = None,
    lock_path: Path | None = None,
    command: list[str] | None = None,
) -> int:
    cwd = cwd or ROOT
    record_dir = record_dir or RECORD_DIR
    sha = _current_sha(cwd)
    record_path = record_dir / f"{sha}.json"
    if record_path.exists():
        print(f"proc_gate run: a record for {sha} already exists; refusing (one run per sha)")
        return 2

    env = dict(os.environ)
    env["TRESTLE_HOST_GATE"] = "proc"
    env["TRESTLE_PROOF_GATE"] = "host-proc"

    args = default_select_args()
    if select:
        # `--select` items add to the default set (a union, CSC-5): each
        # is appended as its own positional node id / `-k` expression,
        # which pytest always includes in addition to the `-m` filter.
        args = args + list(select)

    bound = fence_mod.HOST_RUN_MAX if host_run_max is None else host_run_max

    def _bounded(a, e):
        # own process group; killed whole at the bound (L.P0-0d.3, HOST_RUN_MAX)
        argv = command if command is not None else [sys.executable, "-m", "pytest", "-q", *a]
        child = subprocess.Popen(  # noqa: S603
            argv,
            cwd=cwd,
            env=e,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = child.communicate(timeout=bound)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate()
            raise host_lock.HostRunTimedOut("HOST run timed out") from None
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise
        return subprocess.CompletedProcess(argv, child.returncode, out, err)

    runner = pytest_runner or _bounded

    # a timeout raises out of the lock context (lock freed) before any record
    with host_lock.hold(worktree=cwd, pip_runner=pip_runner, lock_path=lock_path):
        proc = runner(args, env)

    record = {
        "schema": 1,
        "gate": "host-proc",
        "sha": sha,
        "mode": "run",
        "python": platform.python_version(),
        "platform": sys.platform,
        "results": [],
        "status": "PASSED" if proc.returncode == 0 else "FAILED",
    }
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2))
    print(f"proc_gate run: wrote {record_path}")
    return 0
