"""v0.4 Problem C, step 2: ledgers that stay small.

Process identities go to the run's sidecar (`evidence/processes.ndjson`, compacted past
`procident.COMPACT_ROWS` rows); the ledger keeps the leader's row and one `process_summary`.
Recovery reads ledger and sidecar. `evidence/state.json` follows each state-changing ledger row,
and status() and the waiters read it; a non-terminal state.json whose owner lock is free is not
trusted (the ledger is re-read), and the reaper rewrites a stale one.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine.support import START, FakeHost
from trestle.common import clock
from trestle.common.fsutil import atomic_write_json, read_ndjson
from trestle.common.types import RunView
from trestle.server import ledger as ledger_mod
from trestle.server import procident
from trestle.server.home import file_lock, owner_lock_path
from trestle.server.ledger import RunLedger, ledger_path, read_state, state_path
from trestle.server.main import create_kernel
from trestle.server.procident import Identity, Sidecar, sidecar_path
from trestle.server.reaper import reap_home
from trestle.server.recovery import find_run_dir, recover_run_dir, seed_interrupted_run
from trestle.server.runstate import trusted_state

HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "plugins"
FIXTURE_PLUGINS = HERE.parents[1] / "fixtures" / "plugins"
WAIT_S = 30.0
POLL_S = 0.05
NO_WAIT_MS = 0


def _wait(predicate: Callable[[], bool], bound_s: float = WAIT_S) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_S)
    return predicate()


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    for path in [*PLUGINS.glob("*.py"), FIXTURE_PLUGINS / "echo.py"]:
        shutil.copy(path, home / "plugins" / path.name)
    return home


def _kernel(home: Path) -> Any:
    return create_kernel(home=home, plugin_dirs=[home / "plugins"], skip_recovery=True)


def _finished(kernel: Any, plugin: str, args: dict[str, Any]) -> Path:
    view = kernel.control.run(plugin, args, wait_ms=NO_WAIT_MS)
    assert isinstance(view, RunView), view
    assert _wait(lambda: kernel.control.project.status(view.run_id).state == "succeeded")
    run_dir = find_run_dir(kernel.home, view.run_id)
    assert run_dir is not None
    assert _wait(lambda: not _owner_held(run_dir), 5.0)  # the owner closes it after state.json
    return run_dir


def _owner_held(run_dir: Path) -> bool:
    from trestle.server.home import is_locked

    return is_locked(owner_lock_path(run_dir))


def _kinds(run_dir: Path) -> list[str]:
    return [str(row["kind"]) for row in RunLedger.open(ledger_path(run_dir)).records]


# --- the sidecar ---------------------------------------------------------------------------------


def test_run_spawning_many_short_lived_subprocesses_keeps_a_small_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(procident, "COMPACT_ROWS", 20)
    kernel = _kernel(_home(tmp_path))
    run_dir = _finished(kernel, "spawner", {"batches": 8, "width": 6, "seconds": 0.2})
    kinds = _kinds(run_dir)
    assert len(kinds) <= 16, kinds  # about a dozen rows, however many processes ran
    assert kinds.count("process_identity") == 1  # the leader's, for recovery's branch (i)
    (leader,) = [r for r in RunLedger.open(ledger_path(run_dir)).records if r.get("leader")]
    (summary,) = [
        r for r in RunLedger.open(ledger_path(run_dir)).records if r["kind"] == "process_summary"
    ]
    assert kinds.index("process_summary") < kinds.index("group_stop")
    assert summary["seen"] > 20 and summary["alive"] == 0
    # the sidecar was compacted: fewer rows than identities seen, the leader's always kept
    rows = read_ndjson(sidecar_path(run_dir / "evidence"))
    assert len(rows) < summary["seen"]
    assert (leader["pid"], leader["start"]) in {(r["pid"], r["start"]) for r in rows}


def _ident(pid: int, group: int, *, leader: bool = False) -> Identity:
    return Identity(pid=pid, boot="boot-a", start=START, group=group, leader=leader)


def test_compaction_keeps_the_leader_and_the_live_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(procident, "COMPACT_ROWS", 5)
    host = FakeHost()
    host.add(100, 1, 100)
    for pid in range(101, 109):
        host.add(pid, 100, 100)
    sidecar = Sidecar(tmp_path / "processes.ndjson", "r_x")
    attribution = procident.Attribution(group=100, record=sidecar.append, source=host)
    attribution.attribute_leader(100)
    attribution.observe()
    assert sidecar.rows == 9
    for pid in range(101, 107):
        del host.rows[pid]  # exited: no signal can reach them again
    del host.rows[100]  # the leader is kept even once it has gone
    assert procident.compact_sidecar(attribution, sidecar)
    kept = {row["pid"] for row in read_ndjson(sidecar.path)}
    assert kept == {100, 107, 108} and sidecar.rows == 3
    assert not procident.compact_sidecar(attribution, sidecar)  # under the bound: untouched


def _recorded(home: Path, run_id: str, ledger_rows: list[Identity], side: list[Identity]) -> Path:
    run_dir = seed_interrupted_run(home, run_id, last_kind="started")
    ledger = RunLedger.open(ledger_path(run_dir))
    for ident in ledger_rows:
        ledger.append("process_identity", run_id=run_id, **ident.fields())
    sidecar = Sidecar(sidecar_path(run_dir / "evidence"), run_id)
    for ident in side:
        sidecar.append(ident)
    return run_dir


def test_recovery_reads_identities_from_the_sidecar(tmp_path: Path) -> None:
    """Branch (ii): the leader (ledger) is gone, a member recorded only in the sidecar still runs,
    so the group is not confirmed gone. Branch (iii): a process known only from the sidecar is
    stopped, and the rows recovery writes go to the sidecar."""
    home = tmp_path / "home"
    host = FakeHost()
    host.add(201, 1, 201)  # left the leader's group; only its sidecar row ties it to the run
    gone = _recorded(home, "r_side_ii", [_ident(200, 200, leader=True)], [_ident(201, 201)])
    recover_run_dir(gone, source=host, signaller=host)
    records = RunLedger.open(ledger_path(gone)).records
    (stop,) = [r for r in records if r["kind"] == "group_stop"]
    assert (stop["confirmed_gone"], stop["method"]) == (False, "recovery")
    (summary,) = [r for r in records if r["kind"] == "process_summary"]
    assert (summary["seen"], summary["alive"]) == (2, 1)
    assert not host.sent  # branch (ii) never signals

    host = FakeHost()
    host.add(300, 1, 300)
    host.add(301, 1, 301)  # recorded in the sidecar only
    host.add(302, 301, 301)  # its child, with no row yet
    leader = _ident(300, 300, leader=True)
    live = _recorded(home, "r_side_iii", [leader], [leader, _ident(301, 301)])
    recover_run_dir(live, source=host, signaller=host)
    assert not host.rows  # every attributable process was stopped
    assert 301 in {target for kind, target, *_ in host.sent if kind in {"pid", "group"}}
    ledger_pids = {r["pid"] for r in RunLedger.open(ledger_path(live)).records if "pid" in r}
    assert ledger_pids == {300}  # recovery's own identity rows went to the sidecar
    assert 302 in {r["pid"] for r in read_ndjson(sidecar_path(live / "evidence"))}
    assert _kinds(live)[-1] == "interrupted"
    assert read_state(live) is not None and read_state(live)["state"] == "interrupted"  # type: ignore[index]


# --- state.json ----------------------------------------------------------------------------------


def test_state_json_follows_the_ledger_and_a_stale_one_is_not_trusted(tmp_path: Path) -> None:
    kernel = _kernel(_home(tmp_path))
    run_dir = _finished(kernel, "echo", {"message": "hi"})
    records = RunLedger.open(ledger_path(run_dir)).records
    state = read_state(run_dir)
    assert state is not None
    assert state["state"] == "succeeded" and state["finalized"] is True
    assert state["seq"] == records[-1]["seq"] and state["plugin"] == "echo"
    assert state["owner"] == kernel.server_id

    # the owner died between its terminal row and state.json: state.json says running
    stale = {**state, "state": "running", "finalized": False, "seq": state["seq"] - 1}
    atomic_write_json(state_path(run_dir), stale)
    assert trusted_state(run_dir) is None  # non-terminal and the owner lock is free
    assert kernel.control.project.status(run_dir.name).state == "succeeded"  # the ledger re-read
    with file_lock(owner_lock_path(run_dir)):  # held: a live owner, state.json is trusted
        assert trusted_state(run_dir) == stale
        assert kernel.control.project.status(run_dir.name).state == "running"
    # the reaper rewrites a stale state.json of a run it finds terminal
    from trestle.server.home import write_marker

    write_marker(kernel.home, run_dir.name, {"owner": "gone", "month": run_dir.parent.name})
    reap_home(kernel.home)
    assert read_state(run_dir) == state


def test_status_of_a_live_run_reads_state_json_not_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    gate = tmp_path / "gate"
    view = kernel.control.run("gated", {"gate": str(gate)}, wait_ms=NO_WAIT_MS)
    assert isinstance(view, RunView), view
    assert _wait(lambda: kernel.control.project.status(view.run_id).state == "running")
    opened: list[Path] = []
    real_open = ledger_mod.RunLedger.open.__func__  # type: ignore[attr-defined]

    def counting_open(cls: type[RunLedger], path: Path) -> RunLedger:
        opened.append(path)
        return real_open(cls, path)  # type: ignore[no-any-return]

    monkeypatch.setattr(ledger_mod.RunLedger, "open", classmethod(counting_open))
    for _ in range(5):
        assert kernel.control.project.status(view.run_id).state == "running"
    run_dir = find_run_dir(home, view.run_id)
    assert run_dir is not None and ledger_path(run_dir) not in opened
    monkeypatch.undo()
    gate.write_text("open")
    assert _wait(lambda: kernel.control.project.status(view.run_id).state == "succeeded")
    assert clock.await_poll_interval == 0.25 and clock.poll_interval == 0.05


def test_run_without_state_json_is_read_from_its_ledger(tmp_path: Path) -> None:
    kernel = _kernel(_home(tmp_path))
    run_dir = _finished(kernel, "echo", {"message": "old"})
    state_path(run_dir).unlink()  # a run admitted before v0.4
    assert kernel.control.project.status(run_dir.name).state == "succeeded"
    page = kernel.control.project.query("recent_runs", {})
    assert isinstance(page, dict)
    assert [row["state"] for row in page["items"] if row["run_id"] == run_dir.name] == ["succeeded"]
    assert json.loads(ledger_path(run_dir).read_text().splitlines()[0])["kind"] == "created"
