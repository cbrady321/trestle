"""CS-2 process identity (L.CS-2.1; MC-14, B2-C16, V-2.3): one `process_identity` row per process
the supervisor attributes to a run, the leader's before the first liveness poll and every other
process's before anything signals it or counts it gone; the numeric, zone-free start token; stdin
closed at both spawn sites."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tests.core.spine import support
from tests.proof import mcp_host, tolerances
from trestle.server import procident

MARKER_SLEEP = "import sys, time; time.sleep(float(sys.argv[1]))"


class _FirstPollSpy(subprocess.Popen):  # type: ignore[type-arg]
    """A Popen that notes the ledger kinds standing when the conductor first polls the wrapper."""

    at_first_poll: ClassVar[list[list[str]]] = []
    stdin_of: ClassVar[dict[str, Any]] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        argv = [str(a) for a in (args[0] if args else kwargs.get("args", []))]
        for module in ("trestle.child.main", "trestle.wrapper.main"):
            if module in argv:
                _FirstPollSpy.stdin_of[module] = kwargs.get("stdin")
        super().__init__(*args, **kwargs)

    def poll(self) -> int | None:
        args = self.args if isinstance(self.args, list) else []
        if not getattr(self, "_spied", False) and "trestle.wrapper.main" in args:
            self._spied = True
            _FirstPollSpy.at_first_poll.append(support.kinds(Path(str(args[-1]))))
        return super().poll()


def _end_run(kernel: Any, run_id: str, thread: threading.Thread) -> None:
    kernel.control.cancel(run_id)
    thread.join(timeout=tolerances.JOIN_WAIT_S)
    assert not thread.is_alive(), "conductor never returned"


def test_leader_identity_row_precedes_first_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", _FirstPollSpy)
    _FirstPollSpy.at_first_poll.clear()
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "slow", {"seconds": tolerances.JOIN_WAIT_S * 6})
    thread = support.drive_in_thread(kernel, order)
    run_dir = support.run_dir_of(kernel, order.run_id)
    with support.reaping(order.run_id):
        assert support.wait_until(
            lambda: bool(support.rows_of(run_dir, "process_identity")), tolerances.JOIN_WAIT_S
        )
        # MC-10 order: started < the leader's row < the first poll's observation
        assert _FirstPollSpy.at_first_poll, "the conductor never polled"
        assert _FirstPollSpy.at_first_poll[0] == [
            "created",
            "admitted",
            "started",
            "process_identity",
        ]
        leader_rows = [r for r in support.rows_of(run_dir, "process_identity") if r["leader"]]
        assert len(leader_rows) == 1
        leader = leader_rows[0]
        assert leader["group"] == leader["pid"]  # the wrapper leads its own group
        assert leader["boot"] == procident.boot_id() and leader["boot"]
        assert isinstance(leader["start"], str) and int(leader["start"]) > 0
        # `start` is procident's own reading of the live process, compared as integers
        assert int(leader["start"]) == procident.start_time(leader["pid"])
        _end_run(kernel, order.run_id, thread)


def _identity_by_pid(run_dir: Path) -> dict[int, list[dict[str, Any]]]:
    by_pid: dict[int, list[dict[str, Any]]] = {}
    for row in support.rows_of(run_dir, "process_identity"):
        by_pid.setdefault(int(row["pid"]), []).append(row)
    return by_pid


def test_identity_row_per_attributed_process_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": tolerances.JOIN_WAIT_S * 6})
    run_dir = support.run_dir_of(kernel, order.run_id)
    recorder = support.SignalRecorder(run_dir, order.run_id)
    monkeypatch.setattr(os, "kill", recorder.kill)
    monkeypatch.setattr(os, "killpg", recorder.killpg)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        tree = support.observe_tree(kernel, order.run_id)
        everyone = tree.wrapper | tree.child | tree.descendants
        assert len(tree.descendants) == 2
        # every attributed process has exactly one row, with its own group, before any signal
        assert support.wait_until(
            lambda: set(_identity_by_pid(run_dir)) >= {p.pid for p in everyone},
            tolerances.JOIN_WAIT_S,
        )
        by_pid = _identity_by_pid(run_dir)
        for proc in everyone:
            rows = by_pid[proc.pid]
            assert len(rows) == 1, (proc.argv, rows)
            assert rows[0]["group"] == proc.pgid
            assert int(rows[0]["start"]) == proc.start
            assert rows[0]["leader"] is (proc in tree.wrapper)
        assert not recorder.sent, "a signal was sent before the run was stopped"
        _end_run(kernel, order.run_id, thread)
        # nothing was ever signalled without an identity row (the run's own processes only)
        assert recorder.sent, "the stop sent no signal"
        assert support.unrowed(recorder.sent) == []
        # each process still has exactly one row (a second observation never adds one)
        assert all(len(rows) == 1 for rows in _identity_by_pid(run_dir).values())


def test_planted_signal_without_a_row_fails_the_oracle() -> None:
    planted = [
        support.Signal(15, frozenset({101, 102}), frozenset({101}), 0.0),  # 102 has no row
    ]
    assert support.unrowed(planted) == [(15, 102)]
    assert support.unrowed([support.Signal(9, frozenset({101}), frozenset({101}), 0.0)]) == []


_TOKEN_SNIPPET = (
    "import sys; from trestle.server import procident as p; "
    "print(p.boot_id(), p.start_time(int(sys.argv[1])))"
)


def _token_under(pid: int, **env: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", _TOKEN_SNIPPET, str(pid)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _lstart_token(pid: int, **env: str) -> str:
    """The planted implementation: a start string ps formats in local time and locale."""
    proc = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _has_locale(name: str) -> bool:
    listing = subprocess.run(["locale", "-a"], capture_output=True, text=True, check=False)
    return name.lower().replace("-", "") in listing.stdout.lower().replace("-", "")


def test_start_identity_numeric_and_zone_free() -> None:
    proc = subprocess.Popen([sys.executable, "-c", MARKER_SLEEP, str(tolerances.JOIN_WAIT_S)])
    try:
        variants: list[dict[str, str]] = [
            {"TZ": "UTC"},
            {"TZ": "America/New_York"},
            {"TZ": "Asia/Kolkata"},
            {"LC_ALL": "C"},
        ]
        if _has_locale("de_DE.UTF-8"):
            variants.append({"LC_ALL": "de_DE.UTF-8"})
        tokens = {_token_under(proc.pid, **env) for env in variants}
        assert len(tokens) == 1, tokens  # byte-identical under every zone and locale
        boot, start = tokens.pop().rsplit(" ", 1)
        assert boot == procident.boot_id()
        assert start.isdigit() and int(start) == procident.start_time(proc.pid)  # an integer
        # a planted lstart implementation is not zone-free, so the same check would fail it
        planted = {_lstart_token(proc.pid, **env) for env in variants[:3]}
        assert len(planted) > 1, "lstart did not vary with the zone; the planted check is vacuous"
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)


@pytest.mark.proves(
    "WR-CANCEL-5", "WR-CANCEL-5:stdin-not-transport", "core", "core", "PROC+MCP", "BOTH"
)
def test_stub_reading_stdin_gets_eof_not_transport() -> None:
    with mcp_host.McpHost() as host:
        shutil.copy(support.SPINE_PLUGIN_DIR / "stdin_probe.py", host.home / "plugins")
        first = host.call("run", {"plugin": "stdin_probe", "wait_ms": tolerances.HARNESS_WAIT_MS})
        # the stub read EOF at once (within the MC-09 tolerance) and took none of the transport
        assert first["state"] == "succeeded", first
        assert first["summary"] == {"stdin_bytes": 0}, first
        sent_before = host.request_count()
        # the transport is intact: a request after the stub ran is answered, and counted once
        second = host.call("run", {"plugin": "echo", "args": {"message": "after"}})
        assert second["state"] == "succeeded", second
        assert host.request_count() == sent_before + 1


def test_both_spawn_sites_close_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The conductor's Popen and spawn_child's each open stdin on /dev/null, so neither the wrapper
    nor the child can read the host's transport."""
    from trestle.wrapper.spawn import spawn_child

    monkeypatch.setattr(subprocess, "Popen", _FirstPollSpy)
    _FirstPollSpy.stdin_of.clear()
    child = spawn_child(tmp_path)  # fails fast on a run dir with no spec
    child.wait(timeout=tolerances.JOIN_WAIT_S)
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "echo", {"message": "stdin"})
    kernel.control.conductor.drive(order)
    assert _FirstPollSpy.stdin_of == {
        "trestle.child.main": subprocess.DEVNULL,  # spawn_child
        "trestle.wrapper.main": subprocess.DEVNULL,  # the conductor's wrapper
    }
