"""Rig for the PROC falsifiers of L.SL-3.4 / L.SL-3.5: the `proc_leaf` fixture published into a
scratch kernel, a run started but not awaited, and reads of the process table around it.

Every run here is a real run: the kernel admits it, a wrapper and a child process execute the
plugin, and the plugin's `run_tree` acts through the real `CommandPort` / `LocalProcessPort`. Every
process a test starts carries the run's `tag` in its argv, so `ancestry.reap` can clean up whatever
a failing test leaves behind (`tests.core.spine.support.reaping`).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, harness, tolerances
from trestle.common import clock
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[4]
FIXTURE = REPO / "tests" / "fixtures" / "workflows" / "proc_leaf.py"


def fixture_module() -> ModuleType:
    """The fixture file loaded as a module (it is a plugin, not a package member)."""
    spec = importlib.util.spec_from_file_location("proc_leaf_fixture", FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOLDER: str = fixture_module().HOLDER


def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop that keeps the suite fast: both bounds are read at call time from clock.py."""
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def plugin_dir(base: Path, *, budget_s: int | None = None, deadline_s: int | None = None) -> Path:
    """A plugin directory holding the fixture; `budget_s` / `deadline_s` publish a copy with its
    declared budget and deadline rewritten (its three literals, each checked to be there)."""
    directory = base / "plugins"
    directory.mkdir(parents=True, exist_ok=True)
    source = FIXTURE.read_text(encoding="utf-8")
    for literal, value in (("BUDGET_S = 100", budget_s), ("DEADLINE_S = 120", deadline_s)):
        if value is not None:
            assert literal in source
            source = source.replace(literal, f"{literal.split(' = ')[0]} = {value}")
    if deadline_s is not None:
        assert "@trestle(deadline=120," in source
        source = source.replace("@trestle(deadline=120,", f"@trestle(deadline={deadline_s},")
    (directory / "proc_leaf.py").write_text(source, encoding="utf-8")
    return directory


def kernel_over_fixture(
    base: Path, *, budget_s: int | None = None, deadline_s: int | None = None
) -> Kernel:
    directory = plugin_dir(base, budget_s=budget_s, deadline_s=deadline_s)
    return harness.fresh_kernel(plugin_dirs=[directory], home=base / "home")


def tag_for(base: Path, test_name: str) -> str:
    """The marker every process of one test carries: a path under the test's own directory (the
    holder logs the signals it receives beside it) that is unique to the test and longer than
    `support.MIN_MARKER`."""
    return str(base / f"{test_name}-{uuid.uuid4().hex[:8]}")


@contextmanager
def started(
    kernel: Kernel, tag: str, *, reap: bool = True, **args: Any
) -> Iterator[tuple[Any, Path, Any]]:
    """Admit and drive one `proc_leaf` run in a thread. On exit every process of `tag` is killed
    unless `reap` is false (the caller reaps: a found process carries the tag too)."""
    order = support.admit_order(kernel, "proc_leaf", {"env": "e", "tag": tag, **args})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    if not reap:
        yield order, run_dir, thread
        return
    with support.reaping(tag):
        yield order, run_dir, thread


def start_found(tag: str) -> subprocess.Popen[bytes]:
    """A process that already runs the fixture's holder command line: what a run FINDS."""
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, tag],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def signal_log(tag: str, pid: int) -> Path:
    return Path(f"{tag}.{pid}.sig")


def signals_received(tag: str, pid: int) -> list[int]:
    """The catchable stop signals the holder `pid` logged before it exited (none if untouched)."""
    log = signal_log(tag, pid)
    return [int(line) for line in log.read_text().split()] if log.exists() else []


def find(tag: str, pid: int) -> ancestry.ProcInfo | None:
    return next((p for p in support.marked(tag) if p.pid == pid), None)


def wait_for(predicate: Any, what: str) -> None:
    assert support.wait_until(predicate, tolerances.JOIN_WAIT_S * 3), f"never saw: {what}"


def identity_pids(run_dir: Path) -> set[int]:
    return {int(row["pid"]) for row in support.rows_of(run_dir, "process_identity")}


def lane_time(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)
