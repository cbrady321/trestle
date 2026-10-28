"""v0.3.1 Problem A, step 1b: one slot pool per home, shared fairly (rules 4 to 9).

Two-server tests run `pool_server.py` subprocesses (a served kernel each: server lock, reaper, the
250 ms pass) on one `TRESTLE_HOME`, so a server can be SIGSTOPped or SIGKILLed; the rest drive
kernels in process. Runs are `gated`: each holds its slot until the test opens its gate file, so
a test frees one slot at a time and reads who is granted next from `home/sched.json`. A test that
needs a period shorter than the production one (the reserve's 30 s, the reaper's 10 s) passes it
to its server processes only.
"""

from __future__ import annotations

import json
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from trestle.common import codes
from trestle.common.ids import generate_run_id
from trestle.common.types import RequestOutcome, RunView
from trestle.server import pool as pools
from trestle.server.home import read_marker, write_marker
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import Kernel, create_kernel
from trestle.server.reaper import reap_home
from trestle.server.recovery import find_run_dir
from trestle.server.runs import cancel_flag_path

HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "plugins"
SERVER = HERE / "pool_server.py"
REPO = HERE.parents[2]
WAIT_S = 10.0
# rule 5's hand-off bound: one pass (250 ms) plus the candidacy window (1 s), and room for a run's
# own completion and a loaded machine
HANDOFF_S = 2.5
POLL_S = 0.02
QUIET_S = 0.5  # a window in which nothing may start
ANSWER_WAIT_S = 30.0
NO_WAIT_MS = 0


@pytest.fixture
def fast_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")


def _wait(predicate: Callable[[], bool], bound_s: float = WAIT_S) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_S)
    return predicate()


def _home(tmp_path: Path, pool_size: int, extra: str = "") -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(f"max_running_runs = {pool_size}\n{extra}", encoding="utf-8")
    return home


def _sched(home: Path) -> dict[str, Any]:
    return pools.read_sched(home) or pools.empty_state()


def _running(home: Path, server_id: str | None = None) -> list[str]:
    return sorted(
        run_id
        for run_id, entry in _sched(home)["running"].items()
        if server_id is None or entry.get("server") == server_id
    )


def _kinds(home: Path, run_id: str) -> list[str]:
    run_dir = find_run_dir(home, run_id)
    if run_dir is None:
        return []
    return [str(row.get("kind")) for row in RunLedger.open(ledger_path(run_dir)).records]


class Gates:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._n = 0

    def new(self) -> str:
        self._n += 1
        return str(self.root / f"g{self._n}")

    def open(self, gate: str) -> None:
        Path(gate).write_text("1", encoding="utf-8")

    def open_all(self) -> None:
        (self.root / "ALL").write_text("1", encoding="utf-8")


class Server:
    """A `pool_server.py` process on `home`."""

    def __init__(self, home: Path, **overrides: Any) -> None:
        self.home = home
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER), str(home), str(PLUGINS), json.dumps(overrides)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=REPO,
        )
        self.answers: queue.Queue[dict[str, Any]] = queue.Queue()
        self.granted: list[str] = []
        self._ready = threading.Event()
        self.server_id = ""
        threading.Thread(target=self._read, daemon=True).start()
        assert self._ready.wait(30), "pool server did not start"

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            message = json.loads(line)
            if "answer" in message:
                self.answers.put(message["answer"])
            elif message.get("event") == "ready":
                self.server_id = message["server_id"]
                self._ready.set()
            elif message.get("event") == "granted":
                self.granted.append(message["run_id"])

    def send(self, command: dict[str, Any]) -> dict[str, Any]:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(command) + "\n")
        self.proc.stdin.flush()
        return self.answers.get(timeout=ANSWER_WAIT_S)

    def run(self, plugin: str, **args: Any) -> dict[str, Any]:
        return self.send({"op": "run", "plugin": plugin, "args": args})

    def gated(self, gates: Gates, plugin: str = "gated", **args: Any) -> tuple[str, str]:
        gate = gates.new()
        answer = self.run(plugin, gate=gate, **args)
        assert "run_id" in answer, answer
        return answer["run_id"], gate

    def signal(self, signum: int) -> None:
        self.proc.send_signal(signum)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGCONT)
            self.proc.kill()
        self.proc.wait(10)


@pytest.fixture
def servers(tmp_path: Path) -> Iterator[Callable[..., Server]]:
    started: list[Server] = []
    gates = Gates(tmp_path / "gates")

    def start(home: Path, **overrides: Any) -> Server:
        server = Server(home, **overrides)
        started.append(server)
        return server

    try:
        yield start
    finally:
        gates.open_all()  # every gated run of every test ends at once
        for server in started:
            server.kill()


@pytest.fixture
def gates(tmp_path: Path) -> Gates:
    return Gates(tmp_path / "gates")


# --- fair share ----------------------------------------------------------------------------------


def test_one_waiting_run_starts_next_while_another_server_has_50_waiting(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 2)
    a = servers(home)
    a_runs = [a.gated(gates) for _ in range(52)]
    assert _wait(lambda: len(_running(home, a.server_id)) == 2)
    b = servers(home)
    b_run, _ = b.gated(gates)
    time.sleep(QUIET_S)  # absence-window
    assert _running(home) == sorted(run for run, _ in a_runs[:2])  # no free slot: b waits
    gates.open(a_runs[0][1])
    assert _wait(lambda: b_run in _running(home), HANDOFF_S)
    # the freed slot went to the server holding fewer, past a's 50 waiting runs
    assert _running(home) == sorted([a_runs[1][0], b_run])
    keyless = _sched(home)["servers"][a.server_id]["keyless_waiting"]
    assert keyless == 50


def test_two_bursts_on_two_servers_alternate_grants(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 1)
    a, b = servers(home), servers(home)
    owner: dict[str, str] = {}
    gate_of: dict[str, str] = {}
    for _ in range(3):
        for server in (a, b):
            run_id, gate = server.gated(gates)
            owner[run_id], gate_of[run_id] = server.server_id, gate
    order: list[str] = []
    for _ in range(6):
        assert _wait(lambda: len(_running(home)) == 1), _sched(home)
        (run_id,) = _running(home)
        order.append(owner[run_id])
        gates.open(gate_of[run_id])
        assert _wait(lambda run_id=run_id: run_id not in _running(home))  # type: ignore[misc]
    assert all(first != second for first, second in zip(order, order[1:], strict=False)), order


def test_idle_server_keeps_one_slot_and_its_first_run_starts_at_once(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 3)
    a, b = servers(home), servers(home)
    for _ in range(5):
        a.gated(gates)
    assert _wait(lambda: len(_running(home, a.server_id)) == 2)
    time.sleep(2 * QUIET_S)  # absence-window
    assert len(_running(home)) == 2  # one slot stays free for the idle server
    # at once: a's runs hold their slots until the test ends, so only the kept slot can start it
    b_run, _ = b.gated(gates)
    assert _wait(lambda: b_run in _running(home), HANDOFF_S)
    assert len(_running(home, a.server_id)) == 2


def test_reserve_never_takes_the_last_slot(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    """The reserve is capped at max_running_runs - 1: with a pool of 1, an idle server's reserve
    must not block the other server forever."""
    home = _home(tmp_path, 1)
    a, b = servers(home), servers(home)
    assert _wait(lambda: b.server_id in _sched(home)["servers"])
    run_id, _ = a.gated(gates)
    assert _wait(lambda: run_id in _running(home), HANDOFF_S)


def test_max_share_caps_one_server(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path, 4, "[operator]\nmax_share = 2\n")
    kernel = create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)
    try:
        for _ in range(3):
            view = kernel.control.run("gated", {"gate": gates.new()}, wait_ms=NO_WAIT_MS)
            assert isinstance(view, RunView), view
        assert _wait(lambda: len(_running(home)) == 2)
        time.sleep(3 * pools.PASS_INTERVAL_S)  # absence-window: three passes, slots stay free
        assert len(_running(home)) == 2 and len(kernel.control.scheduler.waiting) == 1
    finally:
        gates.open_all()


# --- liveness ------------------------------------------------------------------------------------


def test_idle_server_sigstopped_loses_its_reserve_slot(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    """The reserve lasts while the idle server's seen_at is under 30 s old; shortened to 2 s here
    (and its reaper pass, which writes seen_at, to 0.5 s)."""
    home = _home(tmp_path, 2)
    fast = {"pool": {"RESERVE_FRESH_S": 2.0}, "reap_interval_s": 0.5}
    a, b = servers(home, **fast), servers(home, **fast)
    assert _wait(lambda: b.server_id in _sched(home)["servers"])
    b.signal(signal.SIGSTOP)
    stopped = time.monotonic()
    for _ in range(3):
        a.gated(gates)
    assert _wait(lambda: len(_running(home)) == 1)
    assert len(_running(home)) == 1  # the stopped server's seen_at is still fresh
    assert _wait(lambda: len(_running(home)) == 2, 2.0 + HANDOFF_S)
    assert time.monotonic() - stopped < 2.0 + HANDOFF_S
    assert _running(home) == _running(home, a.server_id)


def test_stopped_server_with_queued_runs_hands_its_turn_on_within_about_1_5s(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 2)
    a = servers(home)
    a_runs = [a.gated(gates) for _ in range(4)]
    assert _wait(lambda: len(_running(home)) == 2)
    b = servers(home)
    b_runs = [b.gated(gates)[0] for _ in range(2)]
    # b is a candidate (fewest slots, a fresh pass) the moment it stops
    assert _wait(lambda: _sched(home)["servers"][b.server_id]["keyless_waiting"] == 2)
    b.signal(signal.SIGSTOP)
    gates.open(a_runs[0][1])
    freed = time.monotonic()
    assert _wait(lambda: a_runs[2][0] in _running(home), HANDOFF_S)
    assert time.monotonic() - freed < HANDOFF_S
    assert not set(b_runs) & set(_running(home))


def test_server_killed_between_grant_and_spawn_keeps_its_slot_until_reaped(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates, fast_stop: None
) -> None:
    home = _home(tmp_path, 2)
    a = servers(home, reap_interval_s=1.0)
    b = servers(home, hold_dispatch=True)
    b_run, _ = b.gated(gates)
    assert _wait(lambda: b.granted == [b_run])  # granted, never spawned
    b.kill()
    first, _ = a.gated(gates)
    second, _ = a.gated(gates)
    assert _wait(lambda: first in _running(home))

    def consistent() -> bool:
        # read the slot first: if a's second run holds one, the dead run was reaped before
        holds = second in _running(home)
        return not holds or "interrupted" in _kinds(home, b_run)

    deadline = time.monotonic() + WAIT_S
    while second not in _running(home) and time.monotonic() < deadline:
        assert consistent()
        time.sleep(POLL_S)
    assert second in _running(home)
    assert "interrupted" in _kinds(home, b_run) and b_run not in _running(home)
    assert "started" not in _kinds(home, b_run)


def test_dead_servers_queued_markers_win_no_grant(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 2)
    a = servers(home, reap_interval_s=60.0)  # a's reaper stays out of the way: markers stay
    a_runs = [a.gated(gates) for _ in range(3)]
    assert _wait(lambda: len(_running(home)) == 2)
    b = servers(home, reap_interval_s=60.0)
    b_runs = [b.gated(gates)[0] for _ in range(2)]
    assert _wait(lambda: _sched(home)["servers"][b.server_id]["keyless_waiting"] == 2)
    b.kill()
    gates.open(a_runs[0][1])
    assert _wait(lambda: a_runs[2][0] in _running(home), HANDOFF_S)
    sched = _sched(home)
    assert b.server_id not in sched["servers"]
    for run_id in b_runs:  # not reaped yet, and never granted
        assert (read_marker(home, run_id) or {}).get("state") == "queued"
        assert run_id not in sched["running"]
    assert sorted(reap_home(home).reaped) == sorted(b_runs)
    for run_id in b_runs:
        assert "started" not in _kinds(home, run_id)


def test_draining_servers_queued_runs_still_win_slots(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 2)
    a, b = servers(home), servers(home)
    a_runs = [a.gated(gates) for _ in range(3)]
    assert _wait(lambda: len(_running(home, a.server_id)) == 1)  # one slot kept for b
    a.signal(signal.SIGTERM)
    # a refusal that mints nothing either way: no such plugin, until the drain refuses first
    assert _wait(lambda: a.run("no_such_plugin").get("code") == codes.SERVICE_DRAINING, 5.0)
    assert _wait(lambda: _sched(home)["servers"][a.server_id]["draining"] is True)
    b_runs = [b.gated(gates)[0] for _ in range(2)]
    assert _wait(lambda: b_runs[0] in _running(home))
    gates.open(a_runs[0][1])
    assert _wait(lambda: a_runs[1][0] in _running(home), HANDOFF_S)
    assert b_runs[1] not in _running(home)


# --- environment keys ----------------------------------------------------------------------------


def test_one_env_key_on_two_servers_runs_one_at_a_time_in_arrival_order(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 4)
    a, b = servers(home), servers(home)
    sent: list[tuple[str, str]] = []
    for server in (a, b, a, b):
        sent.append(server.gated(gates, "env_gated", env="shared"))
    started: list[str] = []
    for _ in sent:
        assert _wait(lambda: len(_running(home)) == 1), _sched(home)
        (run_id,) = _running(home)
        started.append(run_id)
        gate = dict(sent)[run_id]
        time.sleep(pools.PASS_INTERVAL_S)  # absence-window: a pass on each server, still one
        assert _running(home) == [run_id]
        gates.open(gate)
        assert _wait(lambda run_id=run_id: run_id not in _running(home))  # type: ignore[misc]
    assert started == [run_id for run_id, _ in sent]  # created.at order, across both servers


def test_v030_run_holds_its_key_and_takes_no_slot(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path, 1)
    kernel = create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)
    old = generate_run_id()

    def old_run(deadline_in_s: float) -> None:
        marker = {
            "owner": None,  # what `init --upgrade` lists for a v0.3.0 run
            "month": "2026-10",
            "state": "running",
            "lease_key": '"shared"',
            "deadline": time.time() + deadline_in_s,
            "arrival": None,
        }
        write_marker(home, old, marker)
        pools.sched_path(home).unlink(missing_ok=True)  # rebuilt from the markers

    keyed_args = {"env": "shared", "gate": gates.new()}
    try:
        # the busy pre-check counts it: its deadline is past the one a new run would get
        old_run(600)
        busy = kernel.control.run("env_gated", keyed_args, wait_ms=NO_WAIT_MS)
        assert isinstance(busy, RequestOutcome), busy
        assert busy.code == codes.ADMISSION_ENVIRONMENT_BUSY
        old_run(30)
        keyed = kernel.control.run("env_gated", keyed_args, wait_ms=NO_WAIT_MS)
        plain = kernel.control.run("gated", {"gate": gates.new()}, wait_ms=NO_WAIT_MS)
        assert isinstance(keyed, RunView) and isinstance(plain, RunView)
        assert _wait(lambda: plain.run_id in _running(home))  # the one slot was free
        assert _sched(home)["running"][old]["server"] is None
        time.sleep(QUIET_S)  # absence-window
        assert keyed.run_id not in _running(home)  # its key is the old run's
        assert keyed.run_id in [run for run, _ in pools.key_runs(_sched(home), '"shared"')]
        # the old run is reaped (its marker goes): its key is free at the next pass
        (home / "live" / old).unlink()
        gates.open_all()
        assert _wait(lambda: "succeeded" in _kinds(home, keyed.run_id))
    finally:
        gates.open_all()


# --- bounds and cancel ---------------------------------------------------------------------------


def test_full_queue_on_one_server_never_refuses_another_servers_run(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path, 1)
    a = create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)
    b = create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)
    a.control.scheduler.queue_depth = 0  # a's own bound: one slot's worth of runs
    try:
        first = a.control.run("gated", {"gate": gates.new()}, wait_ms=NO_WAIT_MS)
        assert isinstance(first, RunView)
        full = a.control.run("gated", {"gate": gates.new()}, wait_ms=NO_WAIT_MS)
        assert isinstance(full, RequestOutcome) and full.code == codes.QUEUE_FULL
        other = b.control.run("gated", {"gate": gates.new()}, wait_ms=NO_WAIT_MS)
        assert isinstance(other, RunView) and other.state == "queued", other
    finally:
        gates.open_all()
        _settle(a, b)


def test_queued_run_cancelled_by_flag_ends_on_its_owners_pass(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path, 1)
    kernel = create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)
    try:
        holder = kernel.control.run("gated", {"gate": gates.new()}, wait_ms=NO_WAIT_MS)
        waiter = kernel.control.run("gated", {"gate": gates.new()}, wait_ms=NO_WAIT_MS)
        assert isinstance(holder, RunView) and isinstance(waiter, RunView)
        assert _wait(lambda: holder.run_id in _running(home))
        run_dir = find_run_dir(home, waiter.run_id)
        assert run_dir is not None
        # what another server's `cancel` writes: only the flag
        cancel_flag_path(run_dir).write_text("1", encoding="utf-8")
        assert _wait(lambda: "cancelled" in _kinds(home, waiter.run_id), 2.0)
        assert "started" not in _kinds(home, waiter.run_id)
        assert holder.run_id in _running(home)
    finally:
        gates.open_all()


# --- helpers -------------------------------------------------------------------------------------


def _settle(*kernels: Kernel) -> None:
    for kernel in kernels:
        _wait(lambda kernel=kernel: not kernel.control.scheduler.queue)  # type: ignore[misc]
