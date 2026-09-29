"""`fence merge`, `fence ready` and `fence land` driven through `main([...])`
with their real dependencies (git against a temp bare origin; `gh` faked at
the module seam), L.P0-0d.11's CLI paths. No network, no live repo state."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
ENV.update(GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

CI_YML = """\
on: [pull_request, push]
jobs:
  lint: {runs-on: ubuntu-latest}
  ancestry:
    strategy: {matrix: {os: [ubuntu-latest, macos-latest]}}
    runs-on: ${{ matrix.os }}
"""
ALL_OK = {
    "lint": "success",
    "ancestry (ubuntu-latest)": "success",
    "ancestry (macos-latest)": "success",
}


def _sh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _ident(repo: Path) -> None:
    _sh(repo, "config", "user.name", "t")
    _sh(repo, "config", "user.email", "t@t")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def rig(tmp_path: Path, monkeypatch):
    origin = tmp_path / "origin.git"
    _sh(tmp_path, "init", "-q", "--bare", str(origin))
    runner = tmp_path / "runner"
    _sh(tmp_path, "clone", "-q", str(origin), str(runner))
    _ident(runner)
    (runner / "a").mkdir()
    (runner / "a" / "seed.txt").write_text("seed")
    _sh(runner, "add", "a/seed.txt")
    _sh(runner, "commit", "-q", "-m", "root")
    _sh(runner, "push", "-q", "origin", "HEAD:refs/heads/master")
    _sh(runner, "checkout", "-q", "-b", "wr/x/m1")
    (runner / "a" / "change.txt").write_text("x")
    _sh(runner, "add", "a/change.txt")
    _sh(runner, "commit", "-q", "-m", "in-glob change")
    _sh(runner, "push", "-q", "origin", "HEAD:refs/heads/wr/x/m1")
    head = _sh(runner, "rev-parse", "HEAD")

    (tmp_path / "fence.toml").write_text("leave = []\n")
    (tmp_path / "fence.d").mkdir()
    (tmp_path / "fence.d" / "p.toml").write_text(
        'phase = "p"\n[[lane]]\nname = "x"\nbranch_prefix = "wr/x/"\nglobs = ["a/**"]\n'
        '[[gate]]\nbranch = "wr/x/m1"\nmerge = "M1"\n'
    )
    (tmp_path / "ci.yml").write_text(CI_YML)
    monkeypatch.setattr(fence_mod, "ROOT", runner)
    monkeypatch.setattr(fence_mod, "FENCE_PATH", tmp_path / "fence.toml")
    monkeypatch.setattr(fence_mod, "FENCE_D_DIR", tmp_path / "fence.d")
    monkeypatch.setattr(fence_mod, "CI_YML_PATH", tmp_path / "ci.yml")
    state = {"pr_head": head, "conclusions": dict(ALL_OK)}

    def pr_head(_branch, _cwd):
        return state["pr_head"]

    def conclusions(_sha, _cwd):
        value = state["conclusions"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(fence_mod, "pr_head_via_gh", pr_head)
    monkeypatch.setattr(fence_mod, "job_conclusions_via_gh", conclusions)
    return {"origin": origin, "runner": runner, "head": head, "state": state}


def _master_log(rig) -> str:
    return _sh(rig["origin"], "log", "--format=%B", "master")


def _status(rig, capsys) -> list[dict]:
    capsys.readouterr()
    assert fence_mod.main(["ready", "status"]) == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_merge_cli_lands_with_plain_push_and_one_trailer(rig):
    code = fence_mod.main(["merge", "wr/x/m1", "--expect-sha", rig["head"]])
    assert code == 0
    assert _master_log(rig).count("WR-Merge: M1") == 1
    assert _sh(rig["origin"], "rev-parse", "master^2") == rig["head"]


def test_merge_cli_exit_7_without_open_pr_or_on_moved_head(rig):
    rig["state"]["pr_head"] = None
    assert fence_mod.main(["merge", "wr/x/m1", "--expect-sha", rig["head"]]) == 7
    rig["state"]["pr_head"] = "0" * 40
    assert fence_mod.main(["merge", "wr/x/m1", "--expect-sha", rig["head"]]) == 7
    assert "WR-Merge" not in _master_log(rig)


def test_merge_cli_exit_5_when_the_remote_read_fails(rig):
    rig["state"]["conclusions"] = fence_mod.FenceRemoteOutage("gh down")
    assert fence_mod.main(["merge", "wr/x/m1", "--expect-sha", rig["head"]]) == 5


def test_merge_cli_exit_2_failed_job_and_6_unconcluded(rig):
    rig["state"]["conclusions"] = {**ALL_OK, "lint": "failure"}
    assert fence_mod.main(["merge", "wr/x/m1", "--expect-sha", rig["head"]]) == 2
    rig["state"]["conclusions"] = {**ALL_OK, "lint": None}
    assert fence_mod.main(["merge", "wr/x/m1", "--expect-sha", rig["head"]]) == 6
    assert "WR-Merge" not in _master_log(rig)


def test_merge_cli_exit_3_when_master_moved(rig, tmp_path):
    other = tmp_path / "other"
    _sh(tmp_path, "clone", "-q", str(rig["origin"]), str(other))
    _ident(other)
    _sh(other, "checkout", "-q", "-B", "master", "origin/master")
    (other / "moved.txt").write_text("m")
    _sh(other, "add", "moved.txt")
    _sh(other, "commit", "-q", "-m", "out of band")
    _sh(other, "push", "-q", "origin", "HEAD:refs/heads/master")
    assert fence_mod.main(["merge", "wr/x/m1", "--expect-sha", rig["head"]]) == 3


def test_ready_cli_mark_status_unmark_uses_the_gate_merge_id(rig, capsys):
    assert fence_mod.main(["ready", "mark"]) == 0
    (entry,) = _status(rig, capsys)
    assert entry["_merge_id"] == "M1" and entry["state"] == "ready"
    assert entry["head_sha"] == rig["head"]
    assert fence_mod.main(["ready", "unmark"]) == 0
    assert _status(rig, capsys) == []


def test_ready_cli_mark_exits_2_5_6(rig):
    rig["state"]["conclusions"] = {**ALL_OK, "ancestry (macos-latest)": "failure"}
    assert fence_mod.main(["ready", "mark"]) == 2
    rig["state"]["conclusions"] = {"lint": "success"}
    assert fence_mod.main(["ready", "mark"]) == 6
    rig["state"]["conclusions"] = fence_mod.FenceRemoteOutage("gh down")
    assert fence_mod.main(["ready", "mark"]) == 5


def test_ready_cli_clear_lifts_a_block(rig, capsys):
    assert fence_mod.main(["ready", "mark"]) == 0
    state_dir = fence_mod.default_state_dir(rig["runner"])
    for _ in range(fence_mod.LANDING_RETURNS_MAX):
        fence_mod.record_return(state_dir, "M1", "verdict refused")
    (entry,) = _status(rig, capsys)
    assert entry["state"] == "blocked"
    assert fence_mod.main(["ready", "mark", "--clear"]) == 0
    (entry,) = _status(rig, capsys)
    assert entry["state"] == "ready" and entry["returns"] == 0


def test_land_once_lands_the_ready_entry_and_exits(rig, capsys):
    assert fence_mod.main(["ready", "mark"]) == 0
    assert fence_mod.main(["land", "--once"]) == 0
    assert _master_log(rig).count("WR-Merge: M1") == 1
    assert _status(rig, capsys) == []


def test_land_once_on_an_empty_queue_exits_0(rig):
    assert fence_mod.main(["land", "--once"]) == 0


def test_land_once_returns_a_moved_head_once_and_does_not_spin(rig, capsys):
    assert fence_mod.main(["ready", "mark"]) == 0
    rig["state"]["pr_head"] = "0" * 40
    assert fence_mod.main(["land", "--once"]) == 0
    (entry,) = _status(rig, capsys)
    assert entry["returns"] == 1 and entry["reason"] == "head moved during landing"
    assert "WR-Merge" not in _master_log(rig)


def test_land_once_outage_returns_remote_unavailable(rig, capsys):
    assert fence_mod.main(["ready", "mark"]) == 0
    rig["state"]["conclusions"] = fence_mod.FenceRemoteOutage("gh down")
    assert fence_mod.main(["land", "--once"]) == 0
    (entry,) = _status(rig, capsys)
    assert entry["reason"] == "remote unavailable" and entry["returns"] == 1


def test_land_once_ci_that_never_concludes_returns_ci_wait_exceeded(rig, monkeypatch, capsys):
    assert fence_mod.main(["ready", "mark"]) == 0
    rig["state"]["conclusions"] = {"lint": None}
    monkeypatch.setattr(fence_mod, "_time", FakeClock())
    assert fence_mod.main(["land", "--once"]) == 0
    (entry,) = _status(rig, capsys)
    assert entry["reason"] == "CI wait exceeded"
    log = (fence_mod.default_state_dir(rig["runner"]) / "wr-escalations.log").read_text()
    assert "CI wait exceeded" in log


def test_second_land_exits_10_while_the_first_holds_the_lock(rig):
    lock = fence_mod.take_land_lock(fence_mod.default_state_dir(rig["runner"]))
    try:
        assert fence_mod.main(["land", "--once"]) == 10
    finally:
        lock.close()
    assert fence_mod.main(["land", "--once"]) == 0


def test_matrix_job_ancestry_is_required_as_both_check_runs():
    repo_ci = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
    names = fence_mod.required_check_names(repo_ci)
    assert "ancestry" not in names
    assert {"ancestry (ubuntu-latest)", "ancestry (macos-latest)"} <= set(names)
    ok = {n: "success" for n in names}
    assert fence_mod.required_jobs_status(names, ok) == (True, None)
    only_linux = {k: v for k, v in ok.items() if k != "ancestry (macos-latest)"}
    assert fence_mod.required_jobs_status(names, only_linux) == (False, None)
    assert fence_mod.required_jobs_status(names, {**ok, "ancestry (macos-latest)": "failure"}) == (
        False,
        "ancestry (macos-latest)",
    )
