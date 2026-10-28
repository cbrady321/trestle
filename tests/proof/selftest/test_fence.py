"""Selftest for `fence check --pr` / `--history` (CM-2 R1-R6; L.P0-0d.7).

Every case runs in a temp repo with a planted bare origin (no network, no
live repo state — CM-2 planted-case discipline)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
ENV.setdefault("GIT_COMMITTER_NAME", "t")
ENV.setdefault("GIT_COMMITTER_EMAIL", "t@t")
ENV["GIT_COMMITTER_NAME"] = "t"
ENV["GIT_COMMITTER_EMAIL"] = "t@t"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)


def _sh(repo: Path, *args: str) -> str:
    proc = _git(repo, *args)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _commit_file(repo: Path, path: str, content: str, message: str) -> str:
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    _sh(repo, "add", path)
    _sh(repo, "commit", "-m", message)
    return _sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def rig(tmp_path: Path):
    """A bare `origin`, a `master` clone, and a lane clone with a PR
    branch, wired with a minimal fence config (one lane `x`, globs
    `["a/**"]`, one gate `wr/x/m1`)."""
    origin = tmp_path / "origin.git"
    _sh(tmp_path, "init", "-q", "--bare", str(origin))

    master = tmp_path / "master"
    _sh(tmp_path, "clone", "-q", str(origin), str(master))
    (master / "a").mkdir()
    (master / "a" / "seed.txt").write_text("seed")
    _sh(master, "add", "a/seed.txt")
    _sh(master, "commit", "-q", "-m", "root")
    _sh(master, "push", "-q", "origin", "HEAD:refs/heads/master")

    lane = tmp_path / "lane"
    _sh(tmp_path, "clone", "-q", str(origin), str(lane))
    _sh(lane, "checkout", "-q", "-b", "wr/x/m1", "origin/master")

    fence_path = tmp_path / "fence.toml"
    fence_path.write_text('leave = ["leave/**"]\nrecord_exempt = ["records/*.json"]\n')
    d_dir = tmp_path / "fence.d"
    d_dir.mkdir()
    (d_dir / "x.toml").write_text(
        """
phase = "x"
[[lane]]
name = "x"
branch_prefix = "wr/x/"
globs = ["a/**"]
[[gate]]
branch = "wr/x/m1"
merge = "M1"
"""
    )
    cfg = fence_mod.load_fence(fence_path=fence_path, d_dir=d_dir)
    return {"origin": origin, "master": master, "lane": lane, "cfg": cfg}


def _heads(rig):
    base = _sh(rig["master"], "rev-parse", "HEAD")
    _sh(rig["lane"], "fetch", "-q", "origin")
    head = _sh(rig["lane"], "rev-parse", "HEAD")
    return base, head


def test_planted_out_of_glob_path_refused(rig):
    _commit_file(rig["lane"], "outside.txt", "x", "outside the lane's globs")
    base, head = _heads(rig)
    result = fence_mod.check_pr(rig["cfg"], rig["lane"], "wr/x/m1", head, base)
    assert not result.ok and result.rule == "R4"


def test_planted_leave_path_refused(rig):
    _commit_file(rig["lane"], "leave/x.txt", "x", "touches a Leave path")
    base, head = _heads(rig)
    result = fence_mod.check_pr(rig["cfg"], rig["lane"], "wr/x/m1", head, base)
    assert not result.ok and result.rule == "R4"
    assert "Leave" in result.message


def test_planted_missing_predecessor_refused(rig):
    d_dir = rig["cfg"]
    # rebuild config with a gate requiring an unlanded predecessor
    gates = [fence_mod.Gate(branch="wr/x/m1", merge="M1", requires_merge=["M0"])]
    cfg = fence_mod.FenceConfig(
        leave=rig["cfg"].leave,
        record_exempt=rig["cfg"].record_exempt,
        phases={},
        lanes=rig["cfg"].lanes,
        gates=gates,
    )
    _commit_file(rig["lane"], "a/x.txt", "x", "in-glob change")
    base, head = _heads(rig)
    result = fence_mod.check_pr(cfg, rig["lane"], "wr/x/m1", head, base)
    assert not result.ok and result.rule == "R2"
    assert d_dir  # keep reference


def test_branch_rb_1_does_not_match_rb_10_gate():
    gates = [
        fence_mod.Gate(branch="wr/x/rb-1", merge="RB-1"),
        fence_mod.Gate(branch="wr/x/rb-10", merge="RB-10"),
    ]
    assert fence_mod.match_gate("wr/x/rb-1", gates).merge == "RB-1"
    assert fence_mod.match_gate("wr/x/rb-10", gates).merge == "RB-10"


def test_trailer_line_in_pr_commit_refused(rig):
    _commit_file(rig["lane"], "a/x.txt", "x", "WR-Merge: M1")
    base, head = _heads(rig)
    result = fence_mod.check_pr(rig["cfg"], rig["lane"], "wr/x/m1", head, base)
    assert not result.ok and result.rule == "R5"


def test_checkpoint_gate_pr_refused_after_ckpt_succeeded(rig, monkeypatch):
    gates = [fence_mod.Gate(branch="wr/x/j0", merge="J0")]
    cfg = fence_mod.FenceConfig(
        leave=rig["cfg"].leave,
        record_exempt=rig["cfg"].record_exempt,
        phases={},
        lanes=rig["cfg"].lanes,
        gates=gates,
    )
    _commit_file(rig["lane"], "a/x.txt", "x", "in-glob change")
    base, head = _heads(rig)
    monkeypatch.setattr(fence_mod, "ckpt_succeeded", lambda *a, **kw: True)
    result = fence_mod.check_pr(cfg, rig["lane"], "wr/x/j0", head, base)
    assert not result.ok and result.rule == "R6"


def test_branch_behind_origin_master_exits_3(rig):
    # advance origin/master past the lane's base without rebasing the lane
    _commit_file(rig["master"], "a/new.txt", "y", "advance master")
    _sh(rig["master"], "push", "-q", "origin", "HEAD:refs/heads/master")
    new_base = _sh(rig["master"], "rev-parse", "HEAD")
    head = _sh(rig["lane"], "rev-parse", "HEAD")
    result = fence_mod.check_pr(rig["cfg"], rig["lane"], "wr/x/m1", head, new_base)
    assert not result.ok and result.rule == "R3"


def test_history_anomaly_fails(tmp_path):
    repo = tmp_path / "hrepo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "root")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: CS-1")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: CS-1")
    result = fence_mod.check_history("HEAD", repo)
    assert not result.ok


def test_history_check_over_p0(tmp_path):
    repo = tmp_path / "hrepo2"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "root")
    base = _sh(repo, "rev-parse", "HEAD")
    for mid in ("P0-0a", "P0-0b", "P0-0c", "P0-0d"):
        _sh(repo, "commit", "-q", "--allow-empty", "-m", f"WR-Merge: {mid}")
    result = fence_mod.check_history(f"{base}..HEAD", repo)
    assert result.ok


def test_checkpoint_predecessor_reads_newest_successful_carrier(tmp_path):
    repo = tmp_path / "ckptrepo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "root")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J0")
    failed_sha = _sh(repo, "rev-parse", "HEAD")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "retry")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J0")
    newest_sha = _sh(repo, "rev-parse", "HEAD")

    def reader(_cwd, sha, _name):
        return "success" if sha == newest_sha else "failure"

    assert fence_mod.ckpt_succeeded("J0", "HEAD", cwd=repo, check_run_reader=reader) is True
    assert (
        fence_mod.ckpt_succeeded("J0", "HEAD", cwd=repo, check_run_reader=lambda *a: "failure")
        is False
    )
    assert failed_sha  # keeps the earlier carrier referenced


def test_j0_check_run_read_injected_failed_read_is_outage_not_refusal(tmp_path):
    repo = tmp_path / "outagerepo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J0")

    def failing_reader(_cwd, _sha, _name):
        raise fence_mod.FenceRemoteOutage("simulated outage")

    with pytest.raises(fence_mod.FenceRemoteOutage):
        fence_mod.ckpt_succeeded("J0", "HEAD", cwd=repo, check_run_reader=failing_reader)

    def ok_reader(_cwd, _sha, _name):
        return "success"

    assert fence_mod.ckpt_succeeded("J0", "HEAD", cwd=repo, check_run_reader=ok_reader) is True


def test_missing_sha_record_warns(tmp_path):
    repo = tmp_path / "recrepo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "root")
    (repo / "records").mkdir()
    (repo / "records" / "deadbeef.json").write_text(json.dumps({"sha": "deadbeef" * 5}))
    warnings = fence_mod.check_missing_records(repo, ["records/*.json"])
    assert warnings
    assert "deadbeef" in warnings[0]


def test_register_landed_boundary_reads_trailers_landing(tmp_path, monkeypatch):
    from tests.proof import register as register_mod

    repo = tmp_path / "landedrepo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: CS-1")

    from tests.proof import trailers as trailers_mod

    monkeypatch.setattr(trailers_mod, "ROOT", repo)
    assert register_mod.is_landed("CS-1") is True
    assert register_mod.is_landed("CS-2") is False


def test_pr_editing_another_phases_fragment_refused(rig):
    gates = [fence_mod.Gate(branch="wr/x/m1", merge="M1")]
    cfg = fence_mod.FenceConfig(
        leave=rig["cfg"].leave,
        record_exempt=rig["cfg"].record_exempt,
        phases={"x": ["wr/x/"], "y": ["wr/y/"]},
        lanes=rig["cfg"].lanes,
        gates=gates,
    )
    fence_d = rig["lane"] / "tests" / "proof" / "fence.d"
    fence_d.mkdir(parents=True)
    (fence_d / "y.toml").write_text('phase = "y"\n')
    _sh(rig["lane"], "add", "tests/proof/fence.d/y.toml")
    _sh(rig["lane"], "commit", "-q", "-m", "edits another phase's fragment")
    base, head = _heads(rig)
    result = fence_mod.check_pr(cfg, rig["lane"], "wr/x/m1", head, base)
    assert not result.ok and result.rule == "R4"
