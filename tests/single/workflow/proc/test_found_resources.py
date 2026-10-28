"""L.SL-3.5: the PROC falsifiers of A8.1, A8.3 and WR-OWN-7:foreign-listener-blocked.

A resource the run did not create is never the run's to change. Three claims, each asked of real
runs (the `proc_leaf` fixture: a kernel, a wrapper, a child, the real `LocalProcessPort`) and each
read off the found process itself:

- a found process survives every path a run can take: passed, failed, blocked, cancelled, timed
  out, and recovered after the server died. It keeps its pid, start token and command line, and it
  is never signalled (the holder logs every catchable stop signal it receives, whoever sends it;
  a SIGKILL would end it, so "still running" covers the rest);
- a foreign listener on the expected port has no proven identity: it is neither reused nor
  signalled, and the node is blocked (`execution.found_incompatible`) with a human action;
- a found instance that is up but not ready is blocked (`execution.found_unhealthy`) and never
  restarted, recreated or stopped.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, mcp_host, records, tolerances
from tests.single.workflow.proc import procrun
from trestle.common import clock
from trestle.common.types import RunView
from trestle.server.main import Kernel

FOUND_UNHEALTHY = "execution.found_unhealthy"
FOUND_INCOMPATIBLE = "execution.found_incompatible"

# A process that listens on a port of its own choosing (written to a file) and logs any catchable
# stop signal, like the holder. Its command line is not the fixture's: nothing proves who it is.
FOREIGN = (
    "import os, signal, socket, sys; tag, portfile = sys.argv[1], sys.argv[2]; "
    "log = f'{tag}.{os.getpid()}.sig'; "
    "[signal.signal(getattr(signal, n), "
    "lambda s, f: (open(log, 'a').write(f'{s}\\n'), os._exit(0))) "
    "for n in ('SIGTERM', 'SIGINT', 'SIGHUP', 'SIGUSR1', 'SIGUSR2')]; "
    "srv = socket.socket(); srv.bind(('127.0.0.1', 0)); srv.listen(); "
    "open(portfile, 'w').write(str(srv.getsockname()[1])); "
    "[srv.accept()[0].close() for _ in iter(int, 1)]  # trestle foreign listener"
)

PATHS = ("passed", "failed", "blocked", "cancelled", "timed_out", "recovered")


@pytest.fixture(autouse=True)
def _short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    procrun.short_stop(monkeypatch)


class Found:
    """A found process and what it must still be after the run: the same process, unsignalled."""

    def __init__(self, tag: str, proc: subprocess.Popen[bytes]) -> None:
        self.tag = tag
        self.proc = proc
        procrun.wait_for(lambda: procrun.find(tag, proc.pid) is not None, "the found process")
        before = procrun.find(tag, proc.pid)
        assert before is not None
        self.before = before

    def assert_untouched(self) -> None:
        after = procrun.find(self.tag, self.proc.pid)
        assert after is not None, "the found process is gone"
        assert (after.pid, after.start, after.argv) == (
            self.before.pid,
            self.before.start,
            self.before.argv,
        ), "the found process was replaced or its command line changed"
        assert self.proc.poll() is None
        assert procrun.signals_received(self.tag, self.proc.pid) == [], "it was signalled"


def _tickets(run_dir: Path) -> list[dict[str, Any]]:
    return records.lane_tickets(records.lane_rows(run_dir))


def _ends(run_dir: Path) -> list[dict[str, Any]]:
    return [r.entry for r in records.lane_rows(run_dir).rows if r.cls == "end"]


def _to_terminal(kernel: Kernel, args: dict[str, Any]) -> tuple[RunView, Path]:
    view = kernel.control.run(
        plugin="proc_leaf", args={"env": "e", **args}, wait_ms=90_000, completion="terminal"
    )
    assert isinstance(view, RunView), view
    return view, support.run_dir_of(kernel, view.run_id)


def _run_to_a_cancel(kernel: Kernel, tag: str) -> tuple[Path, str]:
    with procrun.started(kernel, tag, reap=False, mode="resource") as (order, run_dir, thread):
        procrun.wait_for(lambda: len(support.marked(tag)) >= 2, "the created process")
        procrun.wait_for(
            lambda: any(r.cls == "confirmation" for r in records.lane_rows(run_dir).rows),
            "the create confirmed",
        )
        kernel.control.cancel(order.run_id)
        thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
        assert not thread.is_alive(), "conductor never returned"
        return run_dir, support.kinds(run_dir)[-1]


def _recovered(tmp_path: Path, tag: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """The server dies while the run's created process runs; the restarted server recovers it."""
    with mcp_host.McpHost(home=tmp_path / "mcp-home") as host:
        (host.home / "plugins" / "proc_leaf.py").write_bytes(procrun.FIXTURE.read_bytes())
        host.restart()  # the server publishes the dropped-in plugin on start
        started = host.call(
            "run",
            {
                "plugin": "proc_leaf",
                "args": {"env": "e", "tag": tag, "mode": "resource"},
                "wait_ms": 0,
            },
        )
        (run_dir,) = sorted((host.home / "runs").glob(f"*/{started['run_id']}"))
        procrun.wait_for(lambda: len(support.marked(tag)) >= 2, "the created process")
        procrun.wait_for(
            lambda: len(support.rows_of(run_dir, "process_identity")) >= 3, "identity rows"
        )
        host.kill_server()
        host.restart()  # recovery runs on start
        procrun.wait_for(lambda: support.kinds(run_dir)[-1] == "interrupted", "recovery")
        return run_dir, support.kinds(run_dir)[-1]


@pytest.mark.proves("A8.1", "A8.1", "A", "single", "PROC", "BOTH")
@pytest.mark.parametrize("path", PATHS)
def test_found_process_untouched_on_every_path(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On every path the found process is the same process, unsignalled. Except on `passed`, where
    the leaf reuses it, the run creates a process of its own with the very same command line, so a
    cleanup that found its target by command line, or swept a group or a pattern, would reach it."""
    tag = procrun.tag_for(tmp_path, path)
    with support.reaping(tag):
        found = Found(tag, procrun.start_found(tag))
        try:
            if path == "timed_out":
                monkeypatch.setattr(clock, "release_slice", support.TEST_GRACE_S)
                monkeypatch.setattr(clock, "FINALIZATION_RESERVE_S", support.TEST_GRACE_S)
                # whole seconds. The wait fits the budget (4 + release 1 <= 5, L.SL-2.1), so the
                # leaf stalls its first observation 6 s: it creates at ~6 s, its wait would end at
                # ~10 s, and the release point (deadline 9 - slice 1) comes first, at 8 s.
                kernel = procrun.kernel_over_fixture(
                    tmp_path, budget_s=5, deadline_s=9, max_wait_s=4, release_s=1
                )
            else:
                kernel = procrun.kernel_over_fixture(tmp_path)

            if path == "recovered":
                run_dir, terminal = _recovered(tmp_path, tag, monkeypatch)
                (stop,) = support.rows_of(run_dir, "group_stop")
                assert (stop["confirmed_gone"], stop["method"]) == (True, "recovery")
                assert terminal == "interrupted"
            elif path == "cancelled":
                run_dir, terminal = _run_to_a_cancel(kernel, tag)
                assert terminal == "cancelled"
            else:
                args: dict[str, Any] = {"tag": tag, "mode": "resource"}
                if path == "passed":
                    args["mode"] = "found"
                if path in ("failed", "blocked"):
                    args["outcome"] = "fail" if path == "failed" else "block"
                if path == "timed_out":
                    args["stall"] = 6
                view, run_dir = _to_terminal(kernel, args)
                terminal = support.kinds(run_dir)[-1]
                assert view.answer is not None
                expected = {"timed_out": "timed_out"}.get(path, "succeeded")
                assert terminal == expected
                assert view.answer["outcome"] == path
                if path == "passed":
                    assert not _tickets(run_dir), "reusing what was found claims nothing"
                    assert view.answer["primary"]["disposition"] == "reused"
                else:
                    assert _tickets(run_dir), "the run acted on a resource of its own"
            found.assert_untouched()
        finally:
            found.proc.kill()
            found.proc.wait()


@pytest.mark.proves("WR-OWN-7", "WR-OWN-7:foreign-listener-blocked", "A", "single", "PROC", "BOTH")
def test_foreign_listener_not_reused_not_signalled(tmp_path: Path) -> None:
    """A process holds the expected port but nothing proves who it is (its command line is not the
    unit's and no record names it): port occupancy alone is not identity. The node is blocked with
    `execution.found_incompatible` and a human action; nothing is created, reused or signalled, and
    the listener still accepts connections."""
    tag = procrun.tag_for(tmp_path, "foreign")
    portfile = tmp_path / "port"
    with support.reaping(tag):
        foreign = subprocess.Popen(
            [sys.executable, "-c", FOREIGN, tag, str(portfile)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            procrun.wait_for(portfile.exists, "the foreign listener's port")
            time.sleep(
                tolerances.SETTLE_SHORT_S
            )  # the port file is written before the first accept
            port = int(portfile.read_text())
            before = procrun.find(tag, foreign.pid)
            assert before is not None
            kernel = procrun.kernel_over_fixture(tmp_path)
            view, run_dir = _to_terminal(kernel, {"tag": tag, "mode": "found", "port": port})

            assert view.answer is not None and view.answer["outcome"] == "blocked"
            primary = view.answer["primary"]
            assert primary["code"] == FOUND_INCOMPATIBLE
            assert primary["human_action"], "a blocked node names the human action"
            assert primary["disposition"] != "reused"
            (end,) = _ends(run_dir)
            assert (end["condition"], end["code"], end["provenance"]) == (
                "incompatible",
                FOUND_INCOMPATIBLE,
                "found",
            )
            assert not _tickets(run_dir), "no effect ran against the occupied port"
            assert not [p for p in support.marked(tag) if p.pid != foreign.pid], "a second process"
            after = procrun.find(tag, foreign.pid)
            assert after is not None
            assert (after.pid, after.start, after.argv) == (before.pid, before.start, before.argv)
            assert foreign.poll() is None
            assert procrun.signals_received(tag, foreign.pid) == []
            with socket.create_connection(("127.0.0.1", port), timeout=tolerances.PROC_WAIT_S):
                pass  # still listening
        finally:
            foreign.kill()
            foreign.wait()


@pytest.mark.proves("A8.3", "A8.3", "A", "single", "PROC", "BOTH")
def test_unhealthy_found_blocked_never_restarted(tmp_path: Path) -> None:
    """A found instance with the unit's own command line that is up but not ready is blocked with
    `execution.found_unhealthy`, naming the human action; the run never restarts, recreates or
    stops it. Once it is healthy the same process is reused, so the block was the health check's."""
    tag = procrun.tag_for(tmp_path, "unhealthy")
    health = tmp_path / "healthy"
    with support.reaping(tag):
        found = Found(tag, procrun.start_found(tag))
        try:
            kernel = procrun.kernel_over_fixture(tmp_path)
            args = {"tag": tag, "mode": "found", "health_file": str(health)}
            view, run_dir = _to_terminal(kernel, args)

            assert view.answer is not None and view.answer["outcome"] == "blocked"
            primary = view.answer["primary"]
            assert (primary["code"], primary["resend"]) == (
                FOUND_UNHEALTHY,
                "succeeds_after_action",
            )
            assert "pre-existing" in primary["human_action"]
            assert primary["disposition"] != "reused"
            assert not _tickets(run_dir), "never restarted, recreated, stopped or replaced"
            found.assert_untouched()
            assert {p.pid for p in support.marked(tag)} == {found.proc.pid}

            health.write_text("ok", encoding="utf-8")  # the human's action
            view, run_dir = _to_terminal(kernel, {**args, "env": "again"})
            assert view.answer is not None and view.answer["outcome"] == "passed"
            assert view.answer["primary"]["disposition"] == "reused"
            assert not _tickets(run_dir)
            found.assert_untouched()
        finally:
            found.proc.kill()
            found.proc.wait()
            ancestry.reap(support.marked(tag))
