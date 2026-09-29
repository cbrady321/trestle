"""Selftest for the CSC-12 HOST lock (L.P0-0d.9)."""

from __future__ import annotations

import os

from tests.proof.host import host_lock


def test_nested_host_lock_does_not_deadlock(tmp_path):
    lock_path = tmp_path / "lock"
    calls = []
    os.environ[host_lock.HELD_ENV] = "1"
    try:
        with host_lock.hold(
            worktree=tmp_path, lock_path=lock_path, pip_runner=lambda *a: calls.append(a)
        ):
            pass
    finally:
        os.environ.pop(host_lock.HELD_ENV, None)
    # nested: the variable was already set, so hold() took no lock and
    # never re-pointed the venv itself.
    assert calls == []
    assert not lock_path.exists()


def test_repoint_runs_under_lock(tmp_path):
    lock_path = tmp_path / "lock"
    calls = []
    os.environ.pop(host_lock.HELD_ENV, None)
    with host_lock.hold(
        worktree=tmp_path, lock_path=lock_path, pip_runner=lambda *a: calls.append(a)
    ):
        assert os.environ.get(host_lock.HELD_ENV) == "1"
        assert len(calls) == 1
    assert host_lock.HELD_ENV not in os.environ
    assert lock_path.exists()


def test_hung_gate_holding_lock_killed_at_host_run_max(tmp_path):
    lock_path = tmp_path / "lock"
    os.environ.pop(host_lock.HELD_ENV, None)
    killed = []
    t = {"n": 0.0}

    def clock():
        t["n"] += 100.0
        return t["n"]

    try:
        with host_lock.hold(
            worktree=tmp_path,
            lock_path=lock_path,
            pip_runner=lambda *a: None,
            host_run_max=50.0,
            clock=clock,
            kill_process_group=lambda: killed.append(True),
        ):
            pass
    except host_lock.HostRunTimedOut:
        pass
    assert killed == [True]
    assert host_lock.HELD_ENV not in os.environ  # freed
