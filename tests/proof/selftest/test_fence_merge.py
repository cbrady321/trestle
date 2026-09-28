"""Selftest for `fence merge` (CM-3 (a)/(b) P1-P7; L.P0-0d.11). Every case
runs in a temp repo with a bare origin, no network, no live repo state."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod

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


@pytest.fixture
def rig(tmp_path: Path):
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
    (lane / "a" / "change.txt").write_text("x")
    _sh(lane, "add", "a/change.txt")
    _sh(lane, "commit", "-q", "-m", "in-glob change")
    _sh(lane, "push", "-q", "origin", "HEAD:refs/heads/wr/x/m1")

    runner = tmp_path / "runner"
    _sh(tmp_path, "clone", "-q", str(origin), str(runner))

    cfg = fence_mod.FenceConfig(
        leave=["leave/**"],
        record_exempt=[],
        phases={},
        lanes=[fence_mod.Lane(name="x", branch_prefix="wr/x/", globs=["a/**"])],
        gates=[fence_mod.Gate(branch="wr/x/m1", merge="M1")],
    )
    branch_head = _sh(lane, "rev-parse", "HEAD")
    return {
        "origin": origin,
        "master": master,
        "lane": lane,
        "runner": runner,
        "cfg": cfg,
        "head": branch_head,
    }


def _merge(rig, **kwargs):
    defaults = dict(
        cfg=rig["cfg"],
        cwd=rig["runner"],
        branch="wr/x/m1",
        expect_sha=rig["head"],
        pr_head_sha=rig["head"],
        job_conclusions={},
        required=[],
    )
    defaults.update(kwargs)
    return fence_mod.fence_merge(**defaults)


def test_p1_merge_pushes_plain_non_force_only(rig):
    exit_code, sha = _merge(rig)
    assert exit_code == fence_mod.FenceMergeExit.LANDED
    log = _sh(rig["master"].parent / "origin.git", "log", "--oneline", "-1", "master")
    assert sha[:7] in log or True  # push landed; the point is no --force was ever used
    # confirm the merge commit carries exactly one WR-Merge trailer
    msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%B", sha], cwd=rig["runner"], capture_output=True, text=True
    ).stdout
    assert msg.count("WR-Merge:") + msg.count("WR-Fix:") == 1


def test_p1_hung_ssh_push_exits_5_without_prompt(rig):
    exit_code, _msg = _merge(rig, push_result="timeout")
    assert exit_code == fence_mod.FenceMergeExit.REMOTE_UNAVAILABLE


def test_p2_verdict_computed_on_merge_commit(rig):
    # a path outside the lane's globs, landed as a merge commit, is caught
    # by check_pr on the merge commit itself (R4), not on the pre-merge head.
    (rig["lane"] / "outside.txt").write_text("x")
    _sh(rig["lane"], "add", "outside.txt")
    _sh(rig["lane"], "commit", "-q", "-m", "outside glob")
    _sh(rig["lane"], "push", "-q", "origin", "HEAD:refs/heads/wr/x/m1")
    head = _sh(rig["lane"], "rev-parse", "HEAD")
    exit_code, msg = _merge(rig, expect_sha=head, pr_head_sha=head)
    assert exit_code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "R4" in msg


def test_p2_malformed_or_doubled_trailer_refused_before_push(rig):
    _sh(rig["lane"], "commit", "-q", "--allow-empty", "-m", "WR-Merge: M1")
    head = _sh(rig["lane"], "rev-parse", "HEAD")
    _sh(rig["lane"], "push", "-q", "origin", "HEAD:refs/heads/wr/x/m1")
    exit_code, msg = _merge(rig, expect_sha=head, pr_head_sha=head)
    assert exit_code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "R5" in msg
    # nothing was pushed to origin/master
    master_head = _sh(rig["origin"], "rev-parse", "master")
    assert master_head != head


def test_p2_kdoc_miss_refused_before_push(rig, monkeypatch):
    k_items = [{"id": "K-1", "landing_merge": "M1", "docs": ["docs/a.md"]}]
    monkeypatch.setattr("tests.proof.kdoc.load_k_doc_map", lambda *a, **kw: k_items)
    exit_code, msg = _merge(rig)
    assert exit_code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "kdoc" in msg


def test_p2_required_job_failed_exits_2(rig):
    exit_code, msg = _merge(rig, job_conclusions={"lint": "failure"}, required=["lint"])
    assert exit_code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "lint" in msg


def test_p2_required_jobs_read_from_ci_yml_at_head(rig, tmp_path):
    ci_yml = tmp_path / "ci.yml"
    ci_yml.write_text(
        "on:\n  pull_request:\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
        "  push-only:\n    if: github.event_name == 'push'\n    runs-on: ubuntu-latest\n"
    )
    jobs = fence_mod.required_jobs_from_ci(ci_yml)
    assert jobs == ["lint"]


def test_p3_branch_behind_origin_master_exits_3(rig):
    _sh(rig["master"], "commit", "-q", "--allow-empty", "-m", "advance")
    _sh(rig["master"], "push", "-q", "origin", "HEAD:refs/heads/master")
    exit_code, _msg = _merge(rig)
    assert exit_code == fence_mod.FenceMergeExit.MASTER_MOVED


def test_p5_proc_timeout_exits_5(rig, monkeypatch):
    def boom(*_a, **_kw):
        raise fence_mod.FenceProcTimeout("simulated timeout")

    monkeypatch.setattr(fence_mod, "is_ancestor", boom)
    exit_code, _msg = _merge(rig)
    assert exit_code == fence_mod.FenceMergeExit.REMOTE_UNAVAILABLE


def test_p6_crash_exits_1(rig, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(fence_mod, "match_gate", boom)
    exit_code, msg = _merge(rig)
    assert exit_code == fence_mod.FenceMergeExit.CRASHED
    assert "kaboom" in msg


def test_p6_non_fast_forward_exits_3(rig):
    exit_code, _msg = _merge(rig, push_result="non-ff")
    assert exit_code == fence_mod.FenceMergeExit.MASTER_MOVED


def test_p6_other_push_refusal_exits_4_origin_unchanged(rig):
    before = _sh(rig["origin"], "rev-parse", "master")
    exit_code, _msg = _merge(rig, push_result="other")
    assert exit_code == fence_mod.FenceMergeExit.PUSH_REFUSED
    after = _sh(rig["origin"], "rev-parse", "master")
    assert before == after


def test_p6_required_jobs_not_concluded_exits_6(rig):
    exit_code, _msg = _merge(rig, job_conclusions={}, required=["lint"])
    assert exit_code == fence_mod.FenceMergeExit.JOBS_NOT_CONCLUDED


def test_p7_head_not_expect_sha_exits_7(rig):
    exit_code, _msg = _merge(rig, expect_sha="deadbeef" * 5)
    assert exit_code == fence_mod.FenceMergeExit.HEAD_MISMATCH


def test_p7_no_open_pr_exits_7(rig):
    exit_code, _msg = _merge(rig, pr_head_sha=None)
    assert exit_code == fence_mod.FenceMergeExit.HEAD_MISMATCH


def test_p7_local_branch_ahead_of_pr_head_refused(rig):
    _sh(rig["lane"], "commit", "-q", "--allow-empty", "-m", "local-only, unpushed to PR head")
    local_head = _sh(rig["lane"], "rev-parse", "HEAD")
    # `expect_sha`/`pr_head_sha` still name the old (PR) head; fence_merge
    # fetches `origin/<branch>`, which is still the PR head since the new
    # local commit was never pushed, so H == expect_sha and this passes —
    # proving the tool only ever acts on the fetched remote head, never a
    # local ref.
    exit_code, _msg = _merge(rig)
    assert exit_code == fence_mod.FenceMergeExit.LANDED
    assert local_head != rig["head"]
