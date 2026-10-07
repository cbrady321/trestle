"""v0.3.1 step 6, Feature 3: `run(after={...})`, a run that waits for another.

A run sent with `after` is admitted at once into the held state (a `held` row after `created`),
where it takes no slot, no environment lease and none of its deadline. Its owner's 250 ms pass reads
the earlier run's `state.json` (and `result.json` for `match`) and appends a `released` row that
mints the run's deadline, or ends the run cancelled, `execution.after_unmet`. Most tests drive
kernels in process; the dead-owner tests run `pool_server.py` subprocesses.
"""

from __future__ import annotations

import json
import signal
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.core.shared_home.test_shared_pool import Gates, Server
from trestle.common import clock, codes
from trestle.common.types import RequestOutcome, RunView, WorkOrder
from trestle.server import chain
from trestle.server import idempotency as keys
from trestle.server import pool as pools
from trestle.server.ledger import NON_TERMINAL_STATES, RunLedger, ledger_path, read_state
from trestle.server.main import Kernel, create_kernel
from trestle.server.reaper import reap_home
from trestle.server.recovery import find_run_dir
from trestle.server.scheduler import Scheduler, _Held

HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "plugins"
WAIT_S = 10.0
POLL_S = 0.02
QUIET_S = 0.6  # a window of a few 250 ms passes in which nothing may happen
NO_WAIT_MS = 0
RUN_WAIT_MS = 20000


def _wait(predicate: Callable[[], bool], bound_s: float = WAIT_S) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_S)
    return predicate()


def _home(tmp_path: Path, pool_size: int = 4, extra: str = "") -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(f"max_running_runs = {pool_size}\n{extra}", encoding="utf-8")
    return home


def _kernel(home: Path) -> Kernel:
    return create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)


def _send(kernel: Kernel, plugin: str = "verdict", **kw: Any) -> RunView:
    args = kw.pop("args", {})
    view = kernel.control.run(plugin, args, wait_ms=NO_WAIT_MS, **kw)
    assert isinstance(view, RunView), view
    return view


def _refused(kernel: Kernel, plugin: str = "verdict", **kw: Any) -> RequestOutcome:
    args = kw.pop("args", {})
    out = kernel.control.run(plugin, args, wait_ms=NO_WAIT_MS, **kw)
    assert isinstance(out, RequestOutcome), out
    return out


def _view(kernel: Kernel, run_id: str) -> RunView:
    view = kernel.control.project.status(run_id)
    assert isinstance(view, RunView), view
    return view


def _state(kernel: Kernel, run_id: str) -> str:
    return _view(kernel, run_id).state


def _await_state(kernel: Kernel, run_id: str, state: str, bound_s: float = WAIT_S) -> RunView:
    assert _wait(lambda: _state(kernel, run_id) == state, bound_s), _view(kernel, run_id)
    return _view(kernel, run_id)


def _ledger(home: Path, run_id: str) -> RunLedger:
    run_dir = find_run_dir(home, run_id)
    assert run_dir is not None
    return RunLedger.open(ledger_path(run_dir))


def _kinds(home: Path, run_id: str) -> list[str]:
    return [str(row["kind"]) for row in _ledger(home, run_id).records]


def _created(home: Path, run_id: str) -> dict[str, Any]:
    created = _ledger(home, run_id).last_kind("created")
    assert created is not None
    return created


def _spec(home: Path, run_id: str) -> dict[str, Any]:
    run_dir = find_run_dir(home, run_id)
    assert run_dir is not None
    return json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))


def _epoch(iso: str) -> float:
    return pools.epoch(iso)


def _sched(home: Path) -> dict[str, Any]:
    return pools.read_sched(home) or pools.empty_state()


@pytest.fixture
def gates(tmp_path: Path) -> Iterator[Gates]:
    opened = Gates(tmp_path / "gates")
    try:
        yield opened
    finally:
        opened.open_all()  # every gated run of every test ends at once


@pytest.fixture
def servers(tmp_path: Path) -> Iterator[Callable[..., Server]]:
    started: list[Server] = []

    def start(home: Path, **overrides: Any) -> Server:
        server = Server(home, **overrides)
        started.append(server)
        return server

    try:
        yield start
    finally:
        Gates(tmp_path / "gates").open_all()
        for server in started:
            server.kill()


# --- the chain ----------------------------------------------------------------------------------


def test_chain_a_then_b_releases_when_a_succeeded(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    gate = gates.new()
    a = _send(kernel, args={"gate": gate, "n": 5})
    b = _send(kernel, args={"n": 6}, after={"run": a.run_id})
    assert b.state == "held" and b.after_run_id == a.run_id
    assert b.to_dict()["after_run_id"] == a.run_id
    assert _view(kernel, a.run_id).to_dict().get("after_run_id") is None
    assert b.state in NON_TERMINAL_STATES
    time.sleep(QUIET_S)  # absence-window
    assert _state(kernel, b.run_id) == "held" and _kinds(home, b.run_id) == ["created", "held"]
    gates.open(gate)

    done = _await_state(kernel, b.run_id, "succeeded")
    assert done.summary == {"n": 6, "ok": True} and done.after_run_id == a.run_id
    kinds = _kinds(home, b.run_id)
    assert kinds[:4] == ["created", "held", "released", "admitted"]
    assert kinds.index("released") < kinds.index("started") < kinds.index("succeeded")
    # the rows: created adds after (canonical JSON), deadline_s and hold_until; released mints
    created = _created(home, b.run_id)
    assert json.loads(created["after"]) == {"run": a.run_id, "when": "succeeded"}
    assert created["after_run_id"] == a.run_id and created["deadline_s"] == 300.0
    released = _ledger(home, b.run_id).last_kind("released")
    assert released is not None and released["reason"] == chain.REASON_MET
    assert _epoch(released["deadline"]) == pytest.approx(_epoch(released["at"]) + 300, abs=2)
    # spec.json stays as admitted: its deadline is the latest possible, hold_until + deadline_s
    spec = _spec(home, b.run_id)
    assert _epoch(spec["deadline"]) == pytest.approx(created["hold_until"] + 300, abs=0.01)
    assert _epoch(spec["deadline"]) < _epoch(released["deadline"]) + 400
    # hold_until: the earlier run's deadline + its recorded margin + 20 s
    a_created = _created(home, a.run_id)
    a_deadline = _epoch(_spec(home, a.run_id)["deadline"])
    assert created["hold_until"] == pytest.approx(
        a_deadline + a_created["finalization_margin_s"] + 20, abs=0.01
    )
    # every reader takes the minted deadline: the lease's, and the terminal wait's
    run_dir = find_run_dir(home, b.run_id)
    assert run_dir is not None
    from trestle.server import lease

    assert lease.deadline_epoch(run_dir) == pytest.approx(_epoch(released["deadline"]), abs=0.01)


def test_match_on_a_failing_result_ends_the_held_run_cancelled_after_unmet(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    gate = gates.new()
    a = _send(kernel, args={"gate": gate, "ok": False})
    b = _send(kernel, after={"run": a.run_id, "match": {"ok": True}})
    gates.open(gate)
    ended = _await_state(kernel, b.run_id, "cancelled")
    assert ended.error is not None
    assert ended.error["code"] == codes.EXECUTION_AFTER_UNMET and ended.error["phase"] == "hold"
    assert (
        a.run_id in ended.error["message"] and chain.REASON_MATCH_MISMATCH in ended.error["message"]
    )
    assert ended.outcome is not None and ended.outcome["class"] == "cancelled"
    assert ended.cleanup is not None and ended.cleanup.processes == "nothing_created"
    kinds = _kinds(home, b.run_id)
    assert "released" not in kinds and "started" not in kinds and kinds[-1] == "cancelled"
    assert read_state(find_run_dir(home, b.run_id))["state"] == "cancelled"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("earlier", "match", "reason"),
    [
        ("boom", {"ok": True}, chain.REASON_EARLIER_NOT_SUCCEEDED),
        ("verdict", {"missing": 1}, chain.REASON_MATCH_MISMATCH),
        ("verdict", {"n": True}, chain.REASON_MATCH_MISMATCH),  # true is not 1
    ],
)
def test_after_unmet_reasons(
    tmp_path: Path, earlier: str, match: dict[str, Any], reason: str
) -> None:
    kernel = _kernel(_home(tmp_path))
    a = kernel.control.run(earlier, {}, wait_ms=RUN_WAIT_MS, completion="terminal")
    assert isinstance(a, RunView)
    b = _send(kernel, after={"run": a.run_id, "match": match})
    ended = _await_state(kernel, b.run_id, "cancelled")
    assert ended.error is not None and reason in ended.error["message"]


def test_match_on_a_result_that_is_absent_is_unmet_with_its_own_reason(tmp_path: Path) -> None:
    kernel = _kernel(_home(tmp_path))
    a = kernel.control.run("verdict", {}, wait_ms=RUN_WAIT_MS, completion="terminal")
    assert isinstance(a, RunView) and a.state == "succeeded"
    run_dir = find_run_dir(kernel.home, a.run_id)
    assert run_dir is not None
    result = run_dir / "evidence" / "result.json"
    for name, reason in (
        ("too_large", chain.REASON_RESULT_TOO_LARGE),
        ("absent", chain.REASON_RESULT_ABSENT),
        ("invalid", chain.REASON_RESULT_INVALID),
    ):
        saved = result.read_bytes() if result.exists() else b""
        if name == "too_large":
            (run_dir / "evidence" / "result.state").write_text("too_large")
        elif name == "absent":
            (run_dir / "evidence" / "result.state").unlink()
            result.unlink()
        else:
            result.write_text("[1]")
        b = _send(kernel, after={"run": a.run_id, "match": {"ok": True}})
        ended = _await_state(kernel, b.run_id, "cancelled")
        assert ended.error is not None and reason in ended.error["message"], (name, ended.error)
        if name == "absent":
            result.write_bytes(saved)


def test_when_ended_releases_on_a_failed_run(tmp_path: Path) -> None:
    kernel = _kernel(_home(tmp_path))
    a = kernel.control.run("boom", {}, wait_ms=RUN_WAIT_MS, completion="terminal")
    assert isinstance(a, RunView) and a.state == "failed"
    after_failed = _send(kernel, args={"n": 2}, after={"run": a.run_id, "when": "ended"})
    assert _await_state(kernel, after_failed.run_id, "succeeded").summary == {"n": 2, "ok": True}
    # the default when: succeeded does not release on a failure
    default = _send(kernel, after={"run": a.run_id})
    assert _await_state(kernel, default.run_id, "cancelled").error is not None


def test_after_key_resolves_to_its_newest_run_and_unknown_is_refused(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    gate = gates.new()
    a = _send(kernel, args={"gate": gate, "n": 9}, idempotency_key="gate:M5")
    b = _send(kernel, after={"key": "gate:M5", "match": {"n": 9}})
    assert b.after_run_id == a.run_id
    gates.open(gate)
    _await_state(kernel, b.run_id, "succeeded")

    for after in ({"run": "r_nothere"}, {"key": "never-used"}):
        out = _refused(kernel, after=after)
        assert out.code == codes.ADMISSION_AFTER_UNKNOWN and out.origin == "admission"
        assert not out.retryable
    assert [r for r in (home / "runs").rglob("r_*") if r.is_dir()].__len__() == 2  # no run id


@pytest.mark.parametrize(
    "after",
    [
        "gate",
        {},
        {"run": "r_x", "key": "k"},
        {"key": 7},
        {"key": "k", "when": "later"},
        {"key": "k", "match": {"ok": True}, "when": "ended"},
        {"key": "k", "unknown": 1},
        {"key": "k", "match": [1]},
    ],
)
def test_malformed_after_is_invalid_args_before_a_run_id(tmp_path: Path, after: Any) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    out = _refused(kernel, after=after)
    assert out.code == codes.INVALID_ARGS
    assert not (home / "runs").exists() or not list((home / "runs").rglob("r_*"))


def test_hold_expired_when_the_earlier_run_outlives_hold_until(
    tmp_path: Path, gates: Gates, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    a = _send(kernel, args={"gate": gates.new()})
    monkeypatch.setattr(chain, "hold_until", lambda earlier_dir, now=None: time.time() + 0.5)
    b = _send(kernel, after={"run": a.run_id})
    ended = _await_state(kernel, b.run_id, "cancelled")
    assert ended.error is not None and ended.error["code"] == codes.EXECUTION_AFTER_UNMET
    assert chain.REASON_HOLD_EXPIRED in ended.error["message"]
    assert _state(kernel, a.run_id) == "running"  # the earlier run is untouched


def test_cancel_accepts_a_held_run_and_it_ends_cancelled_never_started(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    a = _send(kernel, args={"gate": gates.new()})
    b = _send(kernel, after={"run": a.run_id})
    assert b.state == "held"
    accepted = kernel.control.cancel(b.run_id)
    assert accepted.code == codes.CANCEL_ACCEPTED
    ended = _await_state(kernel, b.run_id, "cancelled")
    assert ended.error is not None and ended.error["code"] == codes.EXECUTION_CANCELLED
    assert "held" in ended.error["message"]
    assert "released" not in _kinds(home, b.run_id) and "started" not in _kinds(home, b.run_id)
    assert not kernel.control.scheduler.held and b.run_id not in kernel.control.scheduler.queue
    assert _state(kernel, a.run_id) == "running"


def test_cancel_flag_written_by_another_process_ends_a_held_run_on_the_owners_pass(
    tmp_path: Path, gates: Gates
) -> None:
    from trestle.server.runs import cancel_flag_path

    home = _home(tmp_path)
    kernel = _kernel(home)
    a = _send(kernel, args={"gate": gates.new()})
    b = _send(kernel, after={"run": a.run_id})
    run_dir = find_run_dir(home, b.run_id)
    assert run_dir is not None
    cancel_flag_path(run_dir).write_text("1", encoding="utf-8")  # what another server writes
    _await_state(kernel, b.run_id, "cancelled", 3.0)


# --- capacity ------------------------------------------------------------------------------------


def test_held_run_takes_no_slot_no_lease_and_is_not_counted_in_queue_depth(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path, 1)
    kernel = _kernel(home)
    scheduler = kernel.control.scheduler
    scheduler.queue_depth = 1
    a = _send(kernel, "env_gated", args={"env": "prod", "gate": gates.new()})
    queued = _send(kernel, "gated", args={"gate": gates.new()})
    assert queued.state == "queued"
    full = _refused(kernel, "gated", args={"gate": gates.new()})
    assert full.code == codes.QUEUE_FULL  # the FIFO is full: one running, one waiting
    # held runs are admitted all the same: outside queue_depth, one of them naming the held env
    held = _send(
        kernel, "env_gated", args={"env": "prod", "gate": gates.new()}, after={"run": a.run_id}
    )
    other = _send(kernel, "gated", args={"gate": gates.new()}, after={"run": a.run_id})
    assert held.state == other.state == "held"
    assert len(scheduler.held) == 2
    time.sleep(QUIET_S)  # absence-window
    state = _sched(home)
    assert sorted(state["running"]) == [a.run_id]
    row = state["servers"][kernel.server_id]
    assert row["keyless_waiting"] == 1 and state["waiting_keys"] == {}
    assert pools.used_slots(state) == 1
    # the live markers say held, with an owner lock each
    from trestle.server.home import is_locked, owner_lock_path, read_marker

    for run_id in (held.run_id, other.run_id):
        marker = read_marker(home, run_id)
        assert marker is not None and marker["state"] == "held"
        assert is_locked(owner_lock_path(find_run_dir(home, run_id)))  # type: ignore[arg-type]


def test_max_held_runs_refuses_the_next_after(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path, 4, "[operator]\nmax_held_runs = 2\n")
    kernel = _kernel(home)
    a = _send(kernel, args={"gate": gates.new()})
    first = _send(kernel, after={"run": a.run_id})
    second = _send(kernel, after={"run": a.run_id})
    assert first.state == second.state == "held"
    refused = _refused(kernel, after={"run": a.run_id})
    assert refused.code == codes.QUEUE_FULL and refused.retryable
    # a run without after is unaffected, and cancelling a held run frees its place
    assert _send(kernel, args={"n": 1}).state in {"queued", "running", "succeeded"}
    kernel.control.cancel(first.run_id)
    _await_state(kernel, first.run_id, "cancelled")
    assert _wait(lambda: len(kernel.control.scheduler.held) == 1)
    assert _send(kernel, after={"run": a.run_id}).state == "held"


def test_a_burst_that_fills_one_servers_queue_never_refuses_another_servers_run_or_after(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path, 1)
    a = _kernel(home)
    b = _kernel(home)
    a.control.scheduler.queue_depth = 0  # a's own bound: one slot's worth of runs
    first = _send(a, "gated", args={"gate": gates.new()})
    full = _refused(a, "gated", args={"gate": gates.new()})
    assert full.code == codes.QUEUE_FULL
    # b is not refused: neither a run nor an after, which names a run another server owns
    assert _send(b, "gated", args={"gate": gates.new()}).state == "queued"
    chained = _send(b, "verdict", args={"n": 3}, after={"run": first.run_id})
    assert chained.state == "held"
    # and a's own after is not refused for a's full FIFO either
    assert _send(a, "verdict", after={"run": first.run_id}).state == "held"
    gates.open_all()
    # the earlier run lives on server a: b's pass reads its state.json across servers
    assert _await_state(b, chained.run_id, "succeeded", 20.0).after_run_id == first.run_id


# --- chains, identity, waiting -------------------------------------------------------------------


def test_chain_c_after_b_after_a_runs_in_order_and_bounds_hold_until(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    gate = gates.new()
    a = _send(kernel, args={"gate": gate})
    b = _send(kernel, args={"n": 2}, after={"run": a.run_id})
    c = _send(kernel, args={"n": 3}, after={"run": b.run_id})
    assert (b.state, c.state) == ("held", "held") and c.after_run_id == b.run_id
    b_created, c_created = _created(home, b.run_id), _created(home, c.run_id)
    # a held earlier run: its hold_until + its deadline_s + its margin + 20 s
    assert c_created["hold_until"] == pytest.approx(
        b_created["hold_until"] + b_created["deadline_s"] + b_created["finalization_margin_s"] + 20,
        abs=0.01,
    )
    assert c_created["hold_until"] > b_created["hold_until"]
    time.sleep(QUIET_S)  # absence-window
    assert _state(kernel, c.run_id) == "held"
    gates.open(gate)
    _await_state(kernel, c.run_id, "succeeded", 20.0)
    ended = {
        run_id: _ledger(home, run_id).last_kind("succeeded")["at"]  # type: ignore[index]
        for run_id in (a.run_id, b.run_id, c.run_id)
    }
    assert ended[a.run_id] <= ended[b.run_id] <= ended[c.run_id]
    assert _ledger(home, c.run_id).last_kind("released")["at"] >= ended[b.run_id]  # type: ignore[index]


def test_join_compares_after_beside_args_hash(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    a = _send(kernel, args={"gate": gates.new()})
    other = _send(kernel, args={"n": 7})
    first = _send(kernel, args={"n": 1}, idempotency_key="chain:1", after={"run": a.run_id})
    # the same call joins its run, and the default when is part of the identity, not noise
    again = _send(kernel, args={"n": 1}, idempotency_key="chain:1", after={"run": a.run_id})
    explicit = _send(
        kernel,
        args={"n": 1},
        idempotency_key="chain:1",
        after={"when": "succeeded", "run": a.run_id},
    )
    assert again.run_id == explicit.run_id == first.run_id
    created = _created(home, first.run_id)
    assert json.loads(created["after"]) == {"run": a.run_id, "when": "succeeded"}
    entry = keys.read_entries(home, "chain:1")[0]
    assert entry.after == created["after"] and entry.run_id == first.run_id
    # another after, or none, under the same key is a conflict; args_hash is unchanged by after
    for after in ({"run": other.run_id}, {"run": a.run_id, "when": "ended"}, None):
        conflict = _refused(kernel, args={"n": 1}, idempotency_key="chain:1", after=after)
        assert conflict.code == codes.IDEMPOTENCY_KEY_CONFLICT, after
    plain = _send(kernel, args={"n": 1}, idempotency_key="chain:2")
    assert _created(home, plain.run_id)["args_hash"] == created["args_hash"]
    assert "after" not in _created(home, plain.run_id)


def test_keyed_held_run_window_starts_at_hold_until(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    a = _send(kernel, args={"gate": gates.new()})
    b = _send(kernel, args={"n": 1}, idempotency_key="chain:window", after={"run": a.run_id})
    created = _created(home, b.run_id)
    ttl = created["idempotency_ttl_s"]
    assert created["key_expires_at"] == pytest.approx(
        created["hold_until"] + 300 + clock.finalization_margin + ttl, abs=0.01
    )
    assert keys.read_entries(home, "chain:window")[0].key_expires_at == created["key_expires_at"]


def test_terminal_wait_goes_through_the_hold_and_the_run(tmp_path: Path, gates: Gates) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    gate = gates.new()
    a = _send(kernel, args={"gate": gate})
    threading.Timer(QUIET_S, lambda: gates.open(gate)).start()
    done = kernel.control.run(
        "verdict",
        {"n": 4},
        wait_ms=RUN_WAIT_MS,
        completion="terminal",
        after={"run": a.run_id},
    )
    assert isinstance(done, RunView) and done.state == "succeeded" and done.summary["n"] == 4  # type: ignore[index]
    # the bound is the latest possible one: spec.deadline (hold_until + deadline_s) + margin, and
    # once released the minted deadline's
    bound = kernel.control.project._terminal_bound_s(done.run_id)
    released = _ledger(home, done.run_id).last_kind("released")
    assert released is not None
    assert bound == pytest.approx(
        _epoch(released["deadline"]) - time.time() + clock.finalization_margin, abs=1.5
    )


def test_held_run_is_listed_and_counted_in_recent_runs_and_doctor(
    tmp_path: Path, gates: Gates
) -> None:
    from trestle.server.doctor import build_doctor_report

    home = _home(tmp_path)
    kernel = _kernel(home)
    a = _send(kernel, args={"gate": gates.new()})
    b = _send(kernel, after={"run": a.run_id})
    listing = kernel.control.query("recent_runs", {})
    assert isinstance(listing, dict)
    states = {row["run_id"]: row["state"] for row in listing["items"]}
    assert states[b.run_id] == "held" and a.run_id in states
    report = build_doctor_report(home=home, plugin_dirs=[PLUGINS])
    assert report.run_counts.get("held") == 1 and report.health == "degraded"


def test_a_draining_owner_keeps_checking_and_releases_its_held_run(
    tmp_path: Path, gates: Gates
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    gate = gates.new()
    a = _send(kernel, args={"gate": gate})
    b = _send(kernel, args={"n": 8}, after={"run": a.run_id})
    kernel.control.scheduler.draining = True
    assert _refused(kernel, after={"run": a.run_id}).code == codes.SERVICE_DRAINING
    gates.open(gate)
    assert _await_state(kernel, b.run_id, "succeeded", 20.0).summary == {"n": 8, "ok": True}


# --- vocabulary and pure parts -------------------------------------------------------------------


def test_one_non_terminal_vocabulary_and_the_projected_state_of_a_held_run(tmp_path: Path) -> None:
    assert NON_TERMINAL_STATES == {"queued", "running", "held"}
    ledger = RunLedger.open(tmp_path / "evidence" / "ledger.ndjson")
    ledger.append("created", run_id="r_x")
    assert ledger.projected_state() == "queued"
    ledger.append("held", run_id="r_x")
    assert ledger.projected_state() == "held"
    ledger.append("released", run_id="r_x", deadline="2099-01-01T00:00:00+00:00")
    assert ledger.projected_state() == "queued"
    assert ledger.released_deadline() == "2099-01-01T00:00:00+00:00"
    state = read_state(tmp_path)
    assert state is not None and state["state"] == "queued"
    # a held run that ended without being released is terminal once finalized
    ended = RunLedger.open(tmp_path / "other" / "evidence" / "ledger.ndjson")
    for kind in ("created", "held", "evidence_finalized", "cancelled"):
        ended.append(kind, run_id="r_y")
    assert ended.projected_state() == "cancelled"


def test_released_run_is_inserted_by_arrival_not_appended() -> None:
    scheduler = Scheduler(max_running=0)  # no slot: nothing starts, the FIFO order is visible
    deadline = time.monotonic() + 600

    def order(run_id: str) -> WorkOrder:
        return WorkOrder(run_id=run_id, snapshot_id="s", spec_hash="h")

    scheduler.mint("r_aaaaaaaaaaaaaaa1", "s", "h")
    scheduler.enqueue(order("r_aaaaaaaaaaaaaaa1"), deadline, arrival=1.0, deadline_epoch=1e12)
    scheduler.mint("r_aaaaaaaaaaaaaaa3", "s", "h")
    scheduler.enqueue(order("r_aaaaaaaaaaaaaaa3"), deadline, arrival=3.0, deadline_epoch=1e12)
    scheduler.mint("r_aaaaaaaaaaaaaaa2", "s", "h", held=True)
    scheduler.on_held_check = lambda _order: chain.RELEASE
    scheduler.on_release = lambda _order, _verdict: (deadline, 1e12)
    scheduler._held["r_aaaaaaaaaaaaaaa2"] = _Held(
        order=order("r_aaaaaaaaaaaaaaa2"), key=None, arrival=2.0
    )
    scheduler._check_held()
    assert [entry.order.run_id[-1] for entry in scheduler.waiting] == ["1", "2", "3"]
    assert not scheduler.held and not scheduler._held
    for entry in scheduler.waiting:
        if entry.timer is not None:
            entry.timer.cancel()


def test_parse_after_normalizes_and_names_what_is_wrong() -> None:
    parsed = chain.parse_after({"key": "k", "match": {"ok": True}})
    assert isinstance(parsed, chain.After) and parsed.when == chain.WHEN_SUCCEEDED
    assert parsed.canonical() == '{"key":"k","match":{"ok":true},"when":"succeeded"}'
    assert chain.parse_after({"run": "r", "when": "ended"}) == chain.After(
        run="r", key=None, when="ended", match=None
    )
    for bad in (None, [], {"run": "a", "key": "b"}, {"when": "ended"}, {"run": ""}):
        assert isinstance(chain.parse_after(bad), str), bad


# --- a dead owner ---------------------------------------------------------------------------------


def test_held_run_of_a_dead_owner_is_reaped_interrupted_like_any_run(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 2)
    owner = servers(home)
    a_run, _ = owner.gated(gates)
    held = owner.send({"op": "run", "plugin": "verdict", "args": {}, "after": {"run": a_run}})
    assert held["state"] == "held", held
    assert _wait(lambda: "held" in _kinds(home, held["run_id"]))
    owner.kill()
    reap_home(home)
    assert "interrupted" in _kinds(home, held["run_id"])
    assert "started" not in _kinds(home, held["run_id"])
    run_dir = find_run_dir(home, held["run_id"])
    assert run_dir is not None
    state = read_state(run_dir)
    assert state is not None and state["state"] == "interrupted"
    assert not (home / "live" / held["run_id"]).exists()


def test_earlier_run_reaped_interrupted_leaves_a_succeeded_after_unmet(
    tmp_path: Path, servers: Callable[..., Server], gates: Gates
) -> None:
    home = _home(tmp_path, 2)
    owner = servers(home)
    a_run, _ = owner.gated(gates)
    assert _wait(lambda: a_run in _sched(home)["running"])
    kernel = _kernel(home)  # another server holds the held run
    b = _send(kernel, after={"run": a_run})
    time.sleep(QUIET_S)  # absence-window
    assert _state(kernel, b.run_id) == "held"  # the owner is alive, its state.json non-terminal
    owner.signal(signal.SIGKILL)
    owner.proc.wait(10)
    reap_home(home)
    ended = _await_state(kernel, b.run_id, "cancelled", 5.0)
    assert ended.error is not None and chain.REASON_EARLIER_NOT_SUCCEEDED in ended.error["message"]
    assert "interrupted" in ended.error["message"]
