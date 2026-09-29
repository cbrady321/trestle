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
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from tests.proof import fence as fence_mod
from tests.proof.host import host_lock

ROOT = Path(__file__).resolve().parents[4]
RECORD_DIR = ROOT / "tests" / "proof" / "host" / "host-proc"


def default_set(nodes: list[dict], labels: list[dict]) -> list[str]:
    """CSC-5 default set: every node proving a label whose registered venue
    is BOTH, union every `host_only`-marked node (collection order)."""
    both = {str(lbl["id"]) for lbl in labels if lbl.get("venue") == "BOTH"}
    return [
        n["nodeid"]
        for n in nodes
        if n.get("host_only") or any(lbl in both for lbl in n.get("labels", []))
    ]


def _is_node_id(item: str, cwd: Path) -> bool:
    return "::" in item or item.endswith(".py") or (cwd / item).exists()


def build_node_list(select: list[str] | None, collector, labels: list[dict], cwd: Path):
    """(default node ids, full explicit node-id list). `--select` items add
    to the default set: a node id as is, a `-k` expression resolved by a
    second collect-only pass (union, order-preserving, no duplicates)."""
    default = default_set(collector([]), labels)
    nodes = list(default)
    for item in select or []:
        added = [item] if _is_node_id(item, cwd) else [n["nodeid"] for n in collector(["-k", item])]
        nodes.extend(n for n in added if n not in nodes)
    return default, nodes


_OUTCOME = {
    "passed": "PASSED",
    "failed": "FAILED",
    "skipped": "SKIPPED",
    "xfailed": "XFAIL",
    "xpassed": "XPASS",
}


def _read_audit(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


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
    venv: Path | None = None,
    info_runner=None,
    collector=None,
) -> int:
    cwd = cwd or ROOT
    record_dir = record_dir or RECORD_DIR
    sha = _current_sha(cwd)
    record_path = record_dir / f"{sha}.json"
    if record_path.exists():
        print(f"proc_gate run: a record for {sha} already exists; refusing (one run per sha)")
        return 2

    real_interpreter = pip_runner is None or (pytest_runner is None and command is None)
    if real_interpreter and not host_lock.venv_python(venv).exists():
        # MC-27: no fallback to any other interpreter
        print(f"proc_gate run: clean venv missing: {host_lock.venv_python(venv)}")
        record = {
            "schema": 1,
            "gate": "host-proc",
            "sha": sha,
            "mode": "run",
            "python": "unavailable",
            "platform": "unavailable",
            "results": [],
            "status": "PRECONDITION_UNMET",
        }
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, indent=2))
        return 1

    env = dict(os.environ)
    env["TRESTLE_HOST_GATE"] = "proc"
    env["TRESTLE_PROOF_GATE"] = "host-proc"

    bound = fence_mod.HOST_RUN_MAX if host_run_max is None else host_run_max

    audit_dir = tempfile.mkdtemp(prefix="proc_gate_")
    audit_run = Path(audit_dir) / "run.json"

    env["TRESTLE_AUDIT_OUT"] = str(audit_run)

    def _py_argv(*extra):
        return [*host_lock.venv_command(venv), "-m", "pytest", "-q", *extra]

    def _collect(extra):
        # collect-only in the venv, host_only nodes not deselected (gate env set)
        out = Path(audit_dir) / "collect.json"
        e = dict(env, TRESTLE_AUDIT_OUT=str(out))  # collect writes its own document
        done = subprocess.run(  # noqa: S603
            _py_argv("-p", "tests.proof.audit_plugin", "--collect-only", *extra),
            cwd=cwd,
            env=e,
            capture_output=True,
            text=True,
            check=False,
            timeout=bound,
        )
        data = _read_audit(out)
        if data is None:
            raise RuntimeError(
                "proc_gate run: collect-only produced no audit document "
                f"(exit {done.returncode}): {(done.stdout + done.stderr)[-2000:]}"
            )
        return data["nodes"]

    collect = collector or _collect

    def _bounded(a, e):
        # own process group; killed whole at the bound (L.P0-0d.3, HOST_RUN_MAX)
        argv = command if command is not None else _py_argv("-p", "tests.proof.audit_plugin", *a)
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
    try:
        with host_lock.hold(worktree=cwd, pip_runner=pip_runner, venv=venv, lock_path=lock_path):
            from tests.proof import meta as meta_mod

            default, nodes = build_node_list(select, collect, meta_mod._load_all_labels(), cwd)  # noqa: SLF001
            if nodes:
                proc = runner(nodes, env)
            else:
                proc = None
        audit = _read_audit(audit_run)
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
    if proc is None:
        # nothing selected at all: never a vacuous pass, and not a run
        print("proc_gate run: the default set is empty and no --select was given; nothing ran")
        status = "PRECONDITION_UNMET"
    elif proc.returncode != 0:
        status = "FAILED"
    elif audit is not None and default and not results:
        print("proc_gate run: the default set is non-empty but the run reported 0 results")
        status = "FAILED"
    else:
        status = "PASSED"
    py_version, py_platform = host_lock.venv_interpreter_info(venv, runner=info_runner)
    record = {
        "schema": 1,
        "gate": "host-proc",
        "sha": sha,
        "mode": "run",
        "python": py_version,
        "platform": py_platform,
        "results": results,
        "status": status,
    }
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2))
    print(f"proc_gate run: wrote {record_path}")
    return 0
