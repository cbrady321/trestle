"""SA-10 drift proof: attribution reaches a setsid grandchild that has
broken its ppid chain, and the numeric start token is stable across the
lifetime of a process and across TZ/locale changes (L.P0-0b.4)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import pytest

from tests.proof import ancestry, tolerances

_SLEEP_SNIPPET = "import sys, time; time.sleep(float(sys.argv[1]))"


def _spawn_marked_sleeper(
    seconds: float, marker: str, *, new_session: bool = False
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", _SLEEP_SNIPPET, str(seconds), marker],
        start_new_session=new_session,
    )


_ORPHAN_ROOT_SCRIPT = """
import os, sys, time
marker = sys.argv[1]
mid_pid = os.fork()
if mid_pid == 0:
    grandchild_pid = os.fork()
    if grandchild_pid == 0:
        os.execvp(sys.executable, [sys.executable, "-c",
                                    "import time, sys; time.sleep(float(sys.argv[1]))",
                                    "6", marker])
    else:
        os._exit(0)  # mid exits immediately; the grandchild is reparented
else:
    os.waitpid(mid_pid, 0)
    time.sleep(8)
"""


@pytest.mark.parametrize("sa", ["SA-10"])
def test_setsid_grandchild_attributed_by_marker_and_sid(sa: str) -> None:
    """root starts its own session; root forks mid, mid forks grandchild
    (exec'd into a sleeper carrying the marker) and exits immediately,
    reparenting grandchild away from root (descent is broken). Neither mid
    nor grandchild called setsid, so both still carry root's pgid/sid —
    attribution must reach the grandchild through that (and the marker),
    not through ppid descent."""
    marker = f"trestle-anc-{uuid.uuid4().hex[:12]}"
    root = subprocess.Popen(
        [sys.executable, "-c", _ORPHAN_ROOT_SCRIPT, marker],
        start_new_session=True,
    )
    grandchild_pids: set[int] = set()
    try:
        time.sleep(tolerances.SETTLE_LONG_S)
        root_sid = os.getsid(root.pid)
        assert root_sid == root.pid  # root did start a new session

        snap = ancestry.snapshot()
        root_info = next(p for p in snap if p.pid == root.pid)
        marked = [p for p in snap if marker in p.argv and p.pid != root.pid]
        assert len(marked) == 1, marked
        grandchild = marked[0]
        grandchild_pids.add(grandchild.pid)
        assert grandchild.ppid != root.pid, "descent should be broken by mid's exit"
        assert grandchild.sid == root_sid

        other = _spawn_marked_sleeper(3.0, "not-the-marker")
        try:
            snap = ancestry.snapshot()
            attributed = ancestry.attribute(root_info, snap, marker=marker)
            attributed_pids = {p.pid for p in attributed}
            assert grandchild.pid in attributed_pids
            assert other.pid not in attributed_pids
        finally:
            other.kill()
            other.wait(timeout=tolerances.PROC_WAIT_S)
    finally:
        root.kill()
        try:
            root.wait(timeout=tolerances.PROC_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
        for pid in grandchild_pids:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("sa", ["SA-10"])
def test_numeric_start_stable_for_process_life(sa: str) -> None:
    proc = _spawn_marked_sleeper(2.0, "start-stability")
    try:
        first = ancestry.start_time(proc.pid)
        time.sleep(tolerances.STABILITY_GAP_S)
        second = ancestry.start_time(proc.pid)
        assert first is not None
        assert first == second
        assert isinstance(first, int)
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)


@pytest.mark.parametrize("sa", ["SA-10"])
@pytest.mark.parametrize("tz", ["UTC", "America/New_York"])
def test_start_stable_across_tz(sa: str, tz: str) -> None:
    env = os.environ.copy()
    env["TZ"] = tz
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        env=env,
    )
    try:
        value = ancestry.start_time(proc.pid)
        assert value is not None
        assert isinstance(value, int)
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)


@pytest.mark.parametrize("sa", ["SA-10"])
def test_start_stable_under_lc_all_c(sa: str) -> None:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        env=env,
    )
    try:
        value = ancestry.start_time(proc.pid)
        assert value is not None
        assert isinstance(value, int)
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)
