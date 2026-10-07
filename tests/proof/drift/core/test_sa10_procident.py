"""SA-10 in core (L.CS-2.1): the product's own process identity (`trestle.server.procident`, MC-14)
is a numeric start token that is stable while the process lives and across TZ and LC_ALL, its
`start` parses as an integer, and a pgid is not reused while its group lives."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from tests.proof import tolerances
from trestle.server import procident

_HOLD = "import sys, time; time.sleep(float(sys.argv[1]))"
_TOKEN = (
    "import sys; from trestle.server import procident as p; "
    "print(p.boot_id(), p.start_time(int(sys.argv[1])))"
)
# a member that keeps its group alive after its leader is gone
_LEADER = """
import subprocess, sys
subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
"""


def _sleeper(**kwargs: object) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(tolerances.JOIN_WAIT_S)],
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("sa", ["SA-10"])
def test_start_token_stable_while_process_lives(sa: str) -> None:
    proc = _sleeper()
    try:
        first = procident.start_time(proc.pid)
        time.sleep(tolerances.STABILITY_GAP_S)
        second = procident.start_time(proc.pid)
        assert first is not None and isinstance(first, int)
        assert first == second
        assert procident.SYSTEM.row(proc.pid).start == first  # type: ignore[union-attr]
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)
    assert procident.start_time(proc.pid) is None  # a gone process has no start


@pytest.mark.parametrize("sa", ["SA-10"])
@pytest.mark.parametrize("env", [{"TZ": "UTC"}, {"TZ": "Asia/Kolkata"}, {"LC_ALL": "C"}])
def test_start_token_identical_across_tz_and_lc_all(sa: str, env: dict[str, str]) -> None:
    proc = _sleeper()
    try:
        here = f"{procident.boot_id()} {procident.start_time(proc.pid)}"
        there = subprocess.run(
            [sys.executable, "-c", _TOKEN, str(proc.pid)],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert there == here
        assert there.rsplit(" ", 1)[1].isdigit()  # the start field parses as an integer
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)


@pytest.mark.parametrize("sa", ["SA-10"])
def test_pgid_not_reused_while_its_group_lives(sa: str) -> None:
    leader = subprocess.Popen(
        [sys.executable, "-c", _LEADER, _HOLD, str(tolerances.JOIN_WAIT_S)],
        start_new_session=True,
    )
    group = leader.pid
    try:
        leader.wait(timeout=tolerances.PROC_WAIT_S)  # the leader is gone (and reaped)...
        time.sleep(tolerances.SETTLE_SHORT_S)
        table = procident.SYSTEM.table()
        members = [row for row in table.values() if row.pgid == group]
        assert members, "the group emptied with its leader"
        # ...yet its pid is still the group's id: no process carries it while the group lives
        assert group not in table
        os.killpg(group, 0)
    finally:
        try:
            os.killpg(group, 9)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("sa", ["SA-10"])
def test_linux_stat_parse_survives_hostile_comm(sa: str) -> None:
    raw = "4242 (a) b (c d) S 1 77 77 0 -1 4194304 1 0 0 0 0 0 0 0 20 0 1 0 987654 1 1"
    row = procident.parse_linux_stat(4242, raw)
    assert row == procident.ProcRow(pid=4242, ppid=1, pgid=77, start=987654, zombie=False)
    zombie = procident.parse_linux_stat(9, raw.replace(") S", ") Z"))
    assert zombie is not None and zombie.zombie
    assert procident.parse_linux_stat(9, "garbage") is None
