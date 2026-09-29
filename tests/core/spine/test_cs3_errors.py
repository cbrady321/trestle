"""CS-3 errors explain (L.CS-3.1..3.3; MC-15, MC-CORE-04, MC-CORE-14, SA-13): a failed run's
explanation is written once by the child (`evidence/child_error.json`), folded into the ledger's
`error_record` row (the sole authority), and read back by `RunView.error` and `last_error`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.core.spine.plugins.raiser import SENTINEL as RAISER_SENTINEL
from tests.proof import harness, mcp_host, records, tolerances
from trestle.common import clock, codes, errtext
from trestle.common.types import RunView
from trestle.query.fs import FilesystemQueryBackend
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.recovery import recover_run_dir, seed_interrupted_run

REPO = Path(__file__).resolve().parents[3]
SENTINEL = "SENTINEL-cs3-child-failure-5e2a"

_HEADER = "from trestle.plugin.surface import Context, trestle\n\n"
PLUGINS = {
    "import_error": "import a_module_that_does_not_exist_cs3\n\n"
    + _HEADER
    + "@trestle\ndef import_error(ctx: Context) -> dict[str, int]:\n    return {}\n",
    "missing_arg": _HEADER
    + "@trestle\ndef missing_arg(ctx: Context, required: str) -> dict[str, str]:\n"
    "    return {'got': required}\n",
    "raises": "import os\n\n" + _HEADER + "@trestle\ndef raises(ctx: Context) -> dict[str, int]:\n"
    "    home = os.environ['TRESTLE_HOME']\n"
    "    raise RuntimeError(f'boom at {home}/snapshots in {os.getcwd()}')\n",
    "unencodable": _HEADER
    + "@trestle\ndef unencodable(ctx: Context) -> object:\n    return object()\n",
    "fine": _HEADER
    + "@trestle\ndef fine(ctx: Context) -> dict[str, bool]:\n    return {'ok': True}\n",
}


def run_child(tmp_path: Path, name: str, args: dict[str, Any] | None = None) -> tuple[int, Path]:
    """Run the child entry point alone over a hand-built run directory (no admission, so a plugin
    that admission would refuse still reaches the child)."""
    home = tmp_path / "home"
    snapshot = home / "snapshots" / f"snap_{name}"
    snapshot.mkdir(parents=True)
    (snapshot / "plugin.py").write_text(textwrap.dedent(PLUGINS[name]), encoding="utf-8")
    run_dir = home / "runs" / "2099-01" / f"run_{name}"
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True)
    (run_dir / "work").mkdir()
    spec = {
        "plugin": name,
        "version": "0.1.0",
        "snapshot_id": f"snap_{name}",
        "args": args or {},
        "args_hash": "h",
        "source_sha256": "s",
        "schema_sha256": "s",
        "manifest_sha256": "m",
        "python_version": "3",
        "platform": "p",
        "summary_budget": 1000,
        "timeout_s": 60,
        "resolved_artifacts": {},
    }
    (evidence / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO}{os.pathsep}{REPO / 'packages' / 'trestle-packs'}"
    env["TRESTLE_HOME"] = str(home)
    done = subprocess.run(
        [sys.executable, "-m", "trestle.child.main", "--run-dir", str(run_dir)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        timeout=tolerances.HARNESS_WAIT_MS / 1000,
    )
    return done.returncode, run_dir


def child_error(run_dir: Path) -> dict[str, Any]:
    loaded = json.loads((run_dir / "evidence" / "child_error.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_child_failure_phases_get_distinct_codes(tmp_path: Path) -> None:
    expected = {
        "import_error": ("load", codes.EXECUTION_IMPORT_FAILED),
        "missing_arg": ("bind", codes.EXECUTION_BIND_FAILED),
        "raises": ("call", codes.EXECUTION_PLUGIN_RAISED),
        "unencodable": ("encode", codes.EXECUTION_RESULT_UNENCODABLE),
    }
    seen: dict[str, str] = {}
    for name, (phase, code) in expected.items():
        status, run_dir = run_child(tmp_path / name, name)
        assert status == 1, name  # the exit-code contract is unchanged
        record = child_error(run_dir)
        assert set(record) == {"code", "phase", "message", "exc_type"}
        assert (record["phase"], record["code"]) == (phase, code), name
        assert record["code"] in codes.EXECUTION_CODES
        assert record["message"] and len(record["message"].encode("utf-8")) <= errtext.MESSAGE_MAX
        assert not list((run_dir / "evidence").glob("*.tmp"))  # atomic: no partial left behind
        seen[name] = record["code"]
    assert len(set(seen.values())) == len(expected)


def test_child_failure_message_holds_no_host_path(tmp_path: Path) -> None:
    _status, run_dir = run_child(tmp_path, "raises")
    message = child_error(run_dir)["message"]
    home = tmp_path / "home"
    assert str(home) not in message and str(home.resolve()) not in message
    assert str(run_dir) not in message
    assert "<home>/snapshots" in message and "boom at" in message
    assert "<cwd>" in message  # the work directory the child stands in


def test_child_success_and_no_result_write_no_error(tmp_path: Path) -> None:
    status, run_dir = run_child(tmp_path, "fine")
    assert status == 0
    assert not (run_dir / "evidence" / "child_error.json").exists()
    assert (run_dir / "evidence" / "result.json").exists()


def test_execution_code_vocabulary_is_the_nine_named() -> None:
    names = {
        "import_failed",
        "bind_failed",
        "plugin_raised",
        "result_unencodable",
        "provenance_mismatch",
        "cancelled",
        "deadline_exceeded",
        "worker_exit",
        "interrupted",
    }
    nine = {f"execution.{n}" for n in names}
    # additive since L.SV-4.2 (DM-16): the single-level execution codes join the set; the nine
    # core members are unchanged and no other code is in it
    from trestle.common.plan.vocabulary import SINGLE_LEVEL_CODES

    single = {c for c in SINGLE_LEVEL_CODES if c.startswith("execution.")}
    assert nine <= codes.EXECUTION_CODES
    assert codes.EXECUTION_CODES == nine | single
    for n in names:
        assert getattr(codes, f"EXECUTION_{n.upper()}") == f"execution.{n}"


def test_sanitize_bounds_and_replaces_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = home / "runs" / "2099-01" / "r1"
    cwd = run / "work"
    user = tmp_path / "user"
    roots = {"home": home, "run": run, "cwd": cwd, "user-home": user}
    text = f"a {cwd}/tmp/x b {run}/evidence c {home}/snapshots d {user}/.cfg e {tmp_path}/other"
    out = errtext.sanitize(text, roots)
    # the longest root wins where roots nest: the run directory under the home reads <run>
    want = "a <cwd>/tmp/x b <run>/evidence c <home>/snapshots d <user-home>/.cfg e "
    assert out == want + f"{tmp_path}/other"
    # a prefix only matches at a boundary
    assert errtext.sanitize(f"{home}x/y {home}", roots) == f"{home}x/y <home>"
    # the bound is bytes of UTF-8, cut at a character boundary
    long = "é" * 400  # 800 bytes
    bounded = errtext.sanitize(long, roots)
    assert len(bounded.encode("utf-8")) <= errtext.MESSAGE_MAX
    assert bounded == "é" * (errtext.MESSAGE_MAX // 2)
    odd = errtext.sanitize("a" + "é" * 400, roots)
    assert len(odd.encode("utf-8")) <= errtext.MESSAGE_MAX and odd.startswith("aé")
    odd.encode("utf-8").decode("utf-8")  # whole characters only
    assert errtext.sanitize("short", roots) == "short"
    # a bare-slash root never rewrites every slash
    assert errtext.sanitize("/a/b", {"home": Path("/")}) == "/a/b"


# ---- L.CS-3.2: the ledger's `error_record` row is the sole authority ------------------------


@pytest.fixture
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def _drive(kernel: Any, plugin: str, args: dict[str, Any], *, how: str = "end") -> Path:
    """Run to the terminal row: `end` lets the plugin finish, `cancel` cancels a live tree,
    `deadline` runs a slow plugin under a short deadline. Returns the run directory."""
    if how == "deadline":
        with harness.patch_snapshot(kernel, plugin, timeout_s=support.SHORT_DEADLINE_S):
            order = support.admit_order(kernel, plugin, args)
    else:
        order = support.admit_order(kernel, plugin, args)
    thread = support.drive_in_thread(kernel, order)
    run_dir = support.run_dir_of(kernel, order.run_id)
    if how == "cancel":
        support.wait_ready(run_dir)
        kernel.control.cancel(order.run_id)
    thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound + support.SHORT_DEADLINE_S)
    assert not thread.is_alive(), "the conductor never returned"
    return run_dir


def _row(run_dir: Path) -> dict[str, Any]:
    (row,) = support.rows_of(run_dir, "error_record")
    return row


def _meta_error(run_dir: Path) -> Any:
    meta = json.loads((run_dir / "evidence" / "meta.json").read_text(encoding="utf-8"))
    return meta.get("error")


def _fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("code", "phase", "message")}


def test_error_record_is_ledger_authority_and_survives_restart(short_stop: None) -> None:
    kernel = support.spine_kernel()
    run_dir = _drive(kernel, "raiser", {})
    row = _row(run_dir)
    assert row["code"] == codes.EXECUTION_PLUGIN_RAISED and row["phase"] == "call"
    assert RAISER_SENTINEL in row["message"]
    kinds = support.kinds(run_dir)
    # the explanation is in the ledger before the row that ends execution, after the group stop
    assert kinds.index("group_stop") < kinds.index("error_record") < kinds.index("execution_ended")
    assert records.node_record(run_dir).terminal == "failed"  # an older reader ignores the kind
    assert RunLedger.open(ledger_path(run_dir)).projected_state() == "failed"
    assert _meta_error(run_dir) == _fields(row)
    # a restart rebuilds meta.json from the ledger: the same error, and no second row
    (run_dir / "evidence" / "meta.json").unlink()
    before = support.kinds(run_dir)
    recover_run_dir(run_dir)
    assert support.kinds(run_dir) == before
    assert _meta_error(run_dir) == _fields(row)
    assert _fields(_row(run_dir)) == _fields(row)


def test_error_record_codes_for_cancel_deadline_and_worker_exit(short_stop: None) -> None:
    long_run = {"seconds": tolerances.JOIN_WAIT_S * 6}
    cases = (
        ("cancel", "tree", long_run, "cancel", codes.EXECUTION_CANCELLED, "cancelled"),
        ("deadline", "slow", long_run, "deadline", codes.EXECUTION_DEADLINE_EXCEEDED, "timed_out"),
        ("exit", "exiter", {"status": 3}, "end", codes.EXECUTION_WORKER_EXIT, "worker_exit"),
    )
    seen = set()
    for label, plugin, args, how, code, terminal in cases:
        kernel = support.spine_kernel()
        run_dir = _drive(kernel, plugin, args, how=how)
        assert records.node_record(run_dir).terminal == terminal, label
        row = _row(run_dir)
        assert row["code"] == code and row["code"] in codes.EXECUTION_CODES, label
        assert row["message"] and _meta_error(run_dir) == _fields(row), label
        seen.add(row["code"])
    assert len(seen) == len(cases)


def test_success_writes_no_error_record(short_stop: None) -> None:
    kernel = support.spine_kernel()
    run_dir = _drive(kernel, "echo", {"message": "fine"})
    assert "error_record" not in support.kinds(run_dir)
    assert _meta_error(run_dir) is None
    view = kernel.control.project.status(run_dir.name)
    assert isinstance(view, RunView) and view.state == "succeeded"


def test_recovery_appends_interrupted_error_record_when_absent(tmp_path: Path) -> None:
    run_dir = seed_interrupted_run(tmp_path / "home", "r_cs3_interrupted")
    recover_run_dir(run_dir)
    row = _row(run_dir)
    assert row["code"] == codes.EXECUTION_INTERRUPTED and row["phase"] == "recovery"
    kinds = support.kinds(run_dir)
    assert (
        kinds.index("error_record") < kinds.index("evidence_finalized") < kinds.index("interrupted")
    )
    assert _meta_error(run_dir) == _fields(row)
    again = support.kinds(run_dir)
    recover_run_dir(run_dir)  # a second pass finds the terminal row and adds nothing
    assert support.kinds(run_dir) == again


def test_recovery_keeps_the_error_the_run_already_wrote(tmp_path: Path) -> None:
    run_dir = seed_interrupted_run(tmp_path / "home", "r_cs3_kept", last_kind="execution_ended")
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append(
        "error_record",
        run_id="r_cs3_kept",
        code=codes.EXECUTION_PLUGIN_RAISED,
        phase="call",
        message="kept",
    )
    recover_run_dir(run_dir)
    assert _row(run_dir)["message"] == "kept"
    assert _meta_error(run_dir) == {
        "code": codes.EXECUTION_PLUGIN_RAISED,
        "phase": "call",
        "message": "kept",
    }


# ---- L.CS-3.3: RunView.error and last_error read the ledger row -----------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.proves("A9.1", "A9.1", "A", "core", "MCP", "CI")
@pytest.mark.proves(
    "WR-EVID-1", "WR-EVID-1:sentinel-in-error-field-and-last-error", "core", "core", "MCP", "CI"
)
def test_e1_replay_sentinel_in_answer_error_field_and_last_error(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        shutil.copy(support.SPINE_PLUGIN_DIR / "raiser.py", host.home / "plugins")
        view = host.call("run", {"plugin": "raiser", "wait_ms": tolerances.HARNESS_WAIT_MS})
        assert view["state"] == "failed", view
        error = view["error"]
        assert set(error) == {"code", "phase", "message"}
        assert error["code"] == codes.EXECUTION_PLUGIN_RAISED and error["phase"] == "call"
        assert RAISER_SENTINEL in error["message"]
        last = host.call("query", {"view": "last_error", "params": {"run_id": view["run_id"]}})
        (row,) = last["items"]
        assert set(row) == {
            "run_id",
            "event_seq",
            "kind",
            "message",
            "at",
        }  # the shape is unchanged
        assert RAISER_SENTINEL in row["message"] and row["message"] != "failed"
        assert row["kind"] == "failed" and row["run_id"] == view["run_id"]
        # a run that succeeded has neither
        ok = host.call("run", {"plugin": "echo", "args": {"message": "x"}, "wait_ms": 5000})
        assert ok["state"] == "succeeded" and "error" not in ok
        none = host.call("query", {"view": "last_error", "params": {"run_id": ok["run_id"]}})
        assert none["items"] == []


def test_run_view_has_no_error_while_running(short_stop: None) -> None:
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": tolerances.JOIN_WAIT_S * 6})
    thread = support.drive_in_thread(kernel, order)
    run_dir = support.run_dir_of(kernel, order.run_id)
    try:
        support.wait_ready(run_dir)
        live = kernel.control.project.status(order.run_id)
        assert isinstance(live, RunView) and live.state == "running" and live.error is None
    finally:
        kernel.control.cancel(order.run_id)
        thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound + support.SHORT_DEADLINE_S)
    done = kernel.control.project.status(order.run_id)
    assert isinstance(done, RunView) and done.state == "cancelled"
    assert done.error is not None and done.error["code"] == codes.EXECUTION_CANCELLED


def test_last_error_keeps_the_old_text_for_a_run_with_no_error_record(tmp_path: Path) -> None:
    """A run recorded before `error_record` existed reads as it did: the terminal row's text."""
    run_dir = seed_interrupted_run(tmp_path / "home", "r_cs3_old", last_kind="execution_ended")
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append(
        "evidence_finalized", run_id="r_cs3_old", completeness="partial", result_state="absent"
    )
    ledger.append("failed", run_id="r_cs3_old")
    out = FilesystemQueryBackend(tmp_path / "home").query("last_error", {"run_id": "r_cs3_old"})
    assert isinstance(out, dict)
    (row,) = out["items"]
    assert row["message"] == "failed"


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-10", "core", "core", "INSPECT", "CI")
def test_k10_documented() -> None:
    for name in ("agents.md", "agent-console-mcp.md", "operator-sessions-telemetry.md"):
        doc = (REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "K-10" in doc, name
    agents = (REPO_ROOT / "docs" / "agents.md").read_text(encoding="utf-8")
    assert "explains itself (K-10)" in agents and "`RunView.error`" in agents
