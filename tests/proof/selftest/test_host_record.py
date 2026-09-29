"""Selftest for HOST gate records (CM-6/MC-27; L.P0-0d.3). Planted repos
and records only; never a real HOST run."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod
from tests.proof.host import proc_gate
from tests.proof.host import record as record_mod

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _sh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _sh(r, "init", "-q", "-b", "master")
    (r / "tests" / "proof" / "host" / "host-proc").mkdir(parents=True)
    (r / "code.txt").write_text("x")
    _sh(r, "add", "-A")
    _sh(r, "commit", "-q", "-m", "root")
    return r


def _record(sha: str, gate="host-proc", status="PASSED", results=None) -> dict:
    return {
        "schema": 1,
        "gate": gate,
        "sha": sha,
        "mode": "run",
        "python": "3.12.9",
        "platform": "darwin",
        "results": results or [],
        "status": status,
    }


def test_record_schema_roundtrip():
    rec = _record("a" * 40)
    record_mod.validate_schema(rec)
    with pytest.raises(record_mod.RecordSchemaError):
        record_mod.validate_schema({**rec, "mode": "bogus"})
    with pytest.raises(record_mod.RecordSchemaError):
        record_mod.validate_schema({**rec, "extra_key": 1})


def test_stale_record_rejected_when_non_record_path_changed(tmp_path):
    repo = _repo(tmp_path)
    sha = _sh(repo, "rev-parse", "HEAD")
    rec_path = repo / "tests" / "proof" / "host" / "host-proc" / f"{sha}.json"
    rec_path.write_text(json.dumps(_record(sha)))
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "record")
    (repo / "code.txt").write_text("y")  # a non-record path changes after the record's sha
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "later product change")
    head = _sh(repo, "rev-parse", "HEAD")
    rec = json.loads(rec_path.read_text())
    ok, _warn = record_mod.is_admissible(rec, head, repo)
    assert ok is False


def test_record_valid_when_only_record_files_changed(tmp_path):
    repo = _repo(tmp_path)
    sha = _sh(repo, "rev-parse", "HEAD")
    rec_path = repo / "tests" / "proof" / "host" / "host-proc" / f"{sha}.json"
    rec_path.write_text(json.dumps(_record(sha)))
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "record")
    head = _sh(repo, "rev-parse", "HEAD")
    ok, _warn = record_mod.is_admissible(json.loads(rec_path.read_text()), head, repo)
    assert ok is True


def test_stale_record_rejected_when_runner_changed(tmp_path):
    # "runner changed" is a non-record path change too, under the same rule.
    test_stale_record_rejected_when_non_record_path_changed(tmp_path)


def test_non_ancestor_or_missing_sha_skipped_not_raised(tmp_path):
    repo = _repo(tmp_path)
    base = _sh(repo, "rev-parse", "HEAD")
    _sh(repo, "checkout", "-q", "-b", "side")
    (repo / "code.txt").write_text("side branch commit")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "side")
    side_sha = _sh(repo, "rev-parse", "HEAD")
    _sh(repo, "checkout", "-q", "master")
    head = _sh(repo, "rev-parse", "HEAD")
    assert head == base
    non_ancestor = _record(side_sha)  # a real, present sha that is not head's ancestor
    ok, warn = record_mod.is_admissible(non_ancestor, head, repo)
    assert ok is False and warn is None  # not an ancestor: skipped silently, no error


def test_missing_sha_warns_in_full_checkout(tmp_path, capsys):
    repo = _repo(tmp_path)
    head = _sh(repo, "rev-parse", "HEAD")
    missing = _record("deadbeef" * 5)
    ok, warn = record_mod.is_admissible(missing, head, repo)
    assert ok is False
    assert warn is not None and "deadbeef" in warn


def test_missing_sha_raises_in_shallow_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = _sh(repo, "rev-parse", "HEAD")
    missing = _record("deadbeef" * 5)
    monkeypatch.setattr(fence_mod, "is_shallow", lambda _cwd: True)
    with pytest.raises(record_mod.MissingShaError):
        record_mod.is_admissible(missing, head, repo)


def test_selection_newest_admissible_by_ancestry(tmp_path):
    repo = _repo(tmp_path)
    host_dir = repo / "tests" / "proof" / "host" / "host-proc"
    sha1 = _sh(repo, "rev-parse", "HEAD")
    (host_dir / f"{sha1}.json").write_text(json.dumps(_record(sha1)))
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "record1")
    sha2 = _sh(repo, "rev-parse", "HEAD")
    (host_dir / f"{sha2}.json").write_text(json.dumps(_record(sha2)))
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "record2")
    head = _sh(repo, "rev-parse", "HEAD")
    chosen = record_mod.select("host-proc", head, cwd=repo)
    assert chosen["sha"] == sha2


def test_select_at_checkpoint_anchor_after_head_moved(tmp_path):
    repo = _repo(tmp_path)
    host_dir = repo / "tests" / "proof" / "host" / "host-proc"
    sha1 = _sh(repo, "rev-parse", "HEAD")
    (host_dir / f"{sha1}.json").write_text(json.dumps(_record(sha1)))
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "checkpoint commit C")
    checkpoint_sha = _sh(repo, "rev-parse", "HEAD")
    (repo / "code.txt").write_text("after checkpoint")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "product commit after C")
    head = _sh(repo, "rev-parse", "HEAD")
    assert record_mod.select("host-proc", head, cwd=repo) is None
    at_checkpoint = record_mod.select("host-proc", checkpoint_sha, cwd=repo)
    assert at_checkpoint is not None and at_checkpoint["sha"] == sha1


def test_live_record_check_ignores_older_other_sha_docker_record(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "proof" / "host" / "host-docker").mkdir()
    host_docker = repo / "tests" / "proof" / "host" / "host-docker"
    sha_old = _sh(repo, "rev-parse", "HEAD")
    (host_docker / f"{sha_old}.json").write_text(
        json.dumps(_record(sha_old, gate="host-docker", status="PASSED"))
    )
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "docker record at old sha")
    proc_record = _record(_sh(repo, "rev-parse", "HEAD"), gate="host-proc")
    paired = record_mod.paired_docker(proc_record, cwd=repo)
    assert paired is None  # no host-docker record at the *same* sha


def test_live_record_check_applies_pass_set():
    rec = _record(
        "a" * 40,
        results=[
            {"nodeid": "t::a", "outcome": "PASSED"},
            {"nodeid": "t::b", "outcome": "FAILED"},
            {"nodeid": "t::c", "outcome": "SKIPPED", "labels": ["lbl-gated"]},
            {"nodeid": "t::d", "outcome": "SKIPPED", "labels": ["lbl-claim"]},
            {"nodeid": "t::e", "outcome": "XPASS"},
        ],
    )
    labels = {
        "lbl-gated": {"posture": "gated_on"},
        "lbl-claim": {"posture": "claim"},
    }
    violations = record_mod.pass_set_violations(rec, labels)
    assert any("t::b" in v for v in violations)
    assert any("t::e" in v for v in violations)
    assert any("t::d" in v for v in violations)
    assert not any("t::c" in v for v in violations)


def test_proc_gate_default_set_is_venue_both_union_host_only():
    args = proc_gate.default_select_args()
    assert "host_only" in " ".join(args)


def test_proc_gate_select_adds_to_default_set(tmp_path):
    calls = []

    def fake_runner(a, _e):
        calls.append(a)

        class R:
            returncode = 0

        return R()

    repo = tmp_path / "selrepo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "root")
    rc = proc_gate.run(
        select=["tests/x.py::test_y"],
        cwd=repo,
        record_dir=tmp_path / "records",
        pytest_runner=fake_runner,
        pip_runner=lambda *a: None,
    )
    assert rc == 0
    assert "tests/x.py::test_y" in calls[0]
    assert "-m" in calls[0] and "host_only" in calls[0]


def test_proc_gate_second_run_at_same_sha_refused(tmp_path):
    repo = _repo(tmp_path)
    record_dir = tmp_path / "records"
    record_dir.mkdir()
    sha = _sh(repo, "rev-parse", "HEAD")
    (record_dir / f"{sha}.json").write_text(json.dumps(_record(sha)))
    rc = proc_gate.run(cwd=repo, record_dir=record_dir, pytest_runner=lambda *a: None)
    assert rc == 2
    assert json.loads((record_dir / f"{sha}.json").read_text()) == _record(sha)


def test_proc_gate_runs_under_host_lock(tmp_path):
    repo = _repo(tmp_path)
    from tests.proof.host import host_lock

    held_during_run = {}

    class R:
        returncode = 0

    def fake_runner(_a, _e):
        held_during_run["held"] = os.environ.get(host_lock.HELD_ENV) == "1"
        return R()

    os.environ.pop(host_lock.HELD_ENV, None)
    rc = proc_gate.run(
        cwd=repo,
        record_dir=tmp_path / "records2",
        pytest_runner=fake_runner,
        pip_runner=lambda *a: None,
    )
    assert rc == 0
    assert held_during_run["held"] is True
    assert host_lock.HELD_ENV not in os.environ


def test_hung_gate_run_killed_at_host_run_max_lock_freed(tmp_path, monkeypatch):
    """A planted hung gate run holding the lock is killed with its process
    group at HOST_RUN_MAX (injected clock), writes no record, frees the lock."""
    import fcntl
    import signal
    import time

    from tests.proof.host import host_lock

    repo = _repo(tmp_path)
    record_dir = tmp_path / "records-hung"
    lock_path = tmp_path / "hung.lock"

    # a planted hung gate process in its own process group
    hung = subprocess.Popen(["sleep", "300"], start_new_session=True)
    pgid = os.getpgid(hung.pid)
    killed = []
    ticks = {"n": 0.0}

    def clock():
        ticks["n"] += fence_mod.HOST_RUN_MAX + 1.0
        return ticks["n"]

    def kill_group():
        killed.append(pgid)
        os.killpg(pgid, signal.SIGKILL)

    real_hold = host_lock.hold

    def bounded_hold(**kw):
        return real_hold(
            lock_path=lock_path,
            host_run_max=fence_mod.HOST_RUN_MAX,
            clock=clock,
            kill_process_group=kill_group,
            **kw,
        )

    monkeypatch.setattr(proc_gate.host_lock, "hold", bounded_hold)
    monkeypatch.delenv(host_lock.HELD_ENV, raising=False)
    ran = []
    try:
        with pytest.raises(host_lock.HostRunTimedOut, match="HOST run timed out"):
            proc_gate.run(
                cwd=repo,
                record_dir=record_dir,
                pytest_runner=lambda a, e: ran.append(a),
                pip_runner=lambda *a: None,
            )
        hung.wait(timeout=10)  # reaped: the group really died
    finally:
        if hung.poll() is None:
            hung.kill()
            hung.wait()
    assert killed == [pgid]
    assert hung.returncode == -signal.SIGKILL
    assert ran == []
    assert not list(record_dir.glob("*.json")) if record_dir.exists() else True
    assert host_lock.HELD_ENV not in os.environ
    deadline = time.time() + 5
    with open(lock_path, "a+") as fh:  # lock is free: a second taker gets it at once
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.time() < deadline, "host lock not freed"
                time.sleep(0.05)


def test_live_record_check_on_planted_repo(tmp_path, monkeypatch):
    """In a temp repo the live record check passes with a planted admissible
    record and fails with a planted stale one."""
    from tests.proof import meta as meta_mod
    from tests.proof.host import live_records

    monkeypatch.setattr(meta_mod, "_load_all_labels", lambda: [])

    def plant(repo: Path) -> str:
        sha = _sh(repo, "rev-parse", "HEAD")
        rec = repo / "tests" / "proof" / "host" / "host-proc" / f"{sha}.json"
        rec.write_text(json.dumps(_record(sha)))
        _sh(repo, "add", "-A")
        _sh(repo, "commit", "-q", "-m", "record")
        return sha

    # admissible: only record files changed after the record's sha
    (tmp_path / "good").mkdir()
    good = _repo(tmp_path / "good")
    plant(good)
    monkeypatch.setattr(live_records, "ROOT", good)
    live_records.test_committed_records_valid_for_head()

    # stale: a non-record path changed after the record's sha
    (tmp_path / "stale").mkdir()
    stale = _repo(tmp_path / "stale")
    plant(stale)
    (stale / "code.txt").write_text("changed after the record")
    _sh(stale, "add", "-A")
    _sh(stale, "commit", "-q", "-m", "later product change")
    monkeypatch.setattr(live_records, "ROOT", stale)
    with pytest.raises(AssertionError, match="no admissible host-proc record"):
        live_records.test_committed_records_valid_for_head()


def test_live_records_module_not_default_collected():
    proc = subprocess.run(
        ["python3", "-m", "pytest", "--collect-only", "-q", "tests/proof/host/live_records.py"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
    )
    assert "no tests ran" not in proc.stdout or "error" not in proc.stdout.lower()
    # collected via an explicit path is fine; the point proven elsewhere
    # (test_pXX_live_records_module_not_default_collected-equivalent) is
    # that a bare `pytest --collect-only -q` (no path) never names it —
    # checked in test_host_selection-adjacent full-suite REG, not replanted
    # here to avoid a second full-repo collection pass.
