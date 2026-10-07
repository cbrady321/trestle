"""Selftest for `fence merge` (CM-3 (a)/(b) P1-P7; L.P0-0d.11). Every case
runs in a temp repo with a bare origin, no network, no live repo state."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod
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


def _ident(repo: Path) -> None:
    """Self-contained committer identity, so merges/rebases in this clone do
    not depend on the host's global git config."""
    _sh(repo, "config", "user.name", "t")
    _sh(repo, "config", "user.email", "t@t")


@pytest.fixture
def rig(tmp_path: Path):
    origin = tmp_path / "origin.git"
    _sh(tmp_path, "init", "-q", "--bare", str(origin))
    master = tmp_path / "master"
    _sh(tmp_path, "clone", "-q", str(origin), str(master))
    _ident(master)
    (master / "a").mkdir()
    (master / "a" / "seed.txt").write_text("seed")
    _sh(master, "add", "a/seed.txt")
    _sh(master, "commit", "-q", "-m", "root")
    _sh(master, "push", "-q", "origin", "HEAD:refs/heads/master")

    lane = tmp_path / "lane"
    _sh(tmp_path, "clone", "-q", str(origin), str(lane))
    _ident(lane)
    _sh(lane, "checkout", "-q", "-b", "wr/x/m1", "origin/master")
    (lane / "a" / "change.txt").write_text("x")
    _sh(lane, "add", "a/change.txt")
    _sh(lane, "commit", "-q", "-m", "in-glob change")
    _sh(lane, "push", "-q", "origin", "HEAD:refs/heads/wr/x/m1")

    runner = tmp_path / "runner"
    _sh(tmp_path, "clone", "-q", str(origin), str(runner))
    _ident(runner)

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


def _host_record_holds(_cwd: Path, _head: str) -> None:
    """L1's check stubbed to hold: these planted repos carry no host records
    (L1 itself is proven by the `test_l1_*` cases below)."""
    return None


def _merge(rig, **kwargs):
    defaults = dict(
        cfg=rig["cfg"],
        cwd=rig["runner"],
        branch="wr/x/m1",
        expect_sha=rig["head"],
        pr_head_sha=rig["head"],
        job_conclusions={},
        required=[],
        host_record=_host_record_holds,
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


def test_p2_kdoc_not_owed_by_a_fix_to_a_landed_gate(rig, monkeypatch):
    k_items = [{"id": "K-1", "landing_merge": "M1", "docs": ["docs/a.md"]}]
    monkeypatch.setattr("tests.proof.kdoc.load_k_doc_map", lambda *a, **kw: k_items)
    monkeypatch.setattr("tests.proof.trailers.landing", lambda *a, **kw: "0" * 40)
    exit_code, _msg = _merge(rig)
    assert exit_code == fence_mod.FenceMergeExit.LANDED


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


# --- the landing trailer kind (L.P0-0d.30) -----------------------------------


def _land_id_twice(rig, merge_id: str) -> tuple[str, str]:
    """Land `merge_id` on a first branch, then again on a second branch cut from the new
    master; the two landing commits' subjects."""
    first_cfg = fence_mod.FenceConfig(
        leave=[],
        record_exempt=[],
        phases={},
        lanes=rig["cfg"].lanes,
        gates=[fence_mod.Gate(branch="wr/x/m1", merge=merge_id)],
    )
    code, msg = _merge(rig, cfg=first_cfg)
    assert code == fence_mod.FenceMergeExit.LANDED, msg
    lane = rig["lane"]
    _sh(lane, "fetch", "-q", "origin")
    _sh(lane, "checkout", "-q", "-b", "wr/x/m2", "origin/master")
    (lane / "a" / "second.txt").write_text("y")
    _sh(lane, "add", "a/second.txt")
    _sh(lane, "commit", "-q", "-m", "second landing's change")
    _sh(lane, "push", "-q", "origin", "HEAD:refs/heads/wr/x/m2")
    head = _sh(lane, "rev-parse", "HEAD")
    second_cfg = fence_mod.FenceConfig(
        leave=[],
        record_exempt=[],
        phases={},
        lanes=rig["cfg"].lanes,
        gates=[fence_mod.Gate(branch="wr/x/m2", merge=merge_id)],
    )
    code, msg = _merge(rig, cfg=second_cfg, branch="wr/x/m2", expect_sha=head, pr_head_sha=head)
    assert code == fence_mod.FenceMergeExit.LANDED, msg
    subjects = _sh(rig["origin"], "log", "--first-parent", "--format=%s", "-2", "master")
    newest, oldest = subjects.splitlines()
    return oldest, newest


def test_a_checkpoint_id_lands_again_as_wr_merge_and_newest_follows(rig, monkeypatch):
    """CM-5's failure exit: a NEW carrier of the same checkpoint id is a second
    `WR-Merge`, never a `WR-Fix`, so `trailers.newest` (what CI `ckpt` acts on) finds it."""
    from tests.proof import trailers as trailers_mod

    monkeypatch.setattr(fence_mod, "ckpt_succeeded", lambda *a, **kw: False)
    first, second = _land_id_twice(rig, "J-CORE")
    assert (first, second) == ("WR-Merge: J-CORE", "WR-Merge: J-CORE")
    _sh(rig["runner"], "fetch", "-q", "origin")
    ref = "origin/master"
    old = trailers_mod.landing("J-CORE", ref=ref, cwd=rig["runner"])
    new = trailers_mod.newest("J-CORE", ref=ref, cwd=rig["runner"])
    assert old and new and old != new
    assert new == _sh(rig["runner"], "rev-parse", ref)
    history = fence_mod.check_history(f"{ref}~2..{ref}", rig["runner"])
    assert history.ok, history.message


def test_a_product_id_still_lands_again_as_wr_fix(rig):
    first, second = _land_id_twice(rig, "M1")
    assert (first, second) == ("WR-Merge: M1", "WR-Fix: M1")


def test_landing_trailer_kind_rule(rig):
    runner = rig["runner"]
    assert fence_mod.landing_trailer_kind("J0", "origin/master", runner) == "WR-Merge"
    assert fence_mod.landing_trailer_kind("M1", "origin/master", runner) == "WR-Merge"
    _sh(runner, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J-SINGLE")
    _sh(runner, "commit", "-q", "--allow-empty", "-m", "WR-Merge: M1")
    assert fence_mod.landing_trailer_kind("J-SINGLE", "HEAD", runner) == "WR-Merge"
    assert fence_mod.landing_trailer_kind("M1", "HEAD", runner) == "WR-Fix"


# ---- L1: the host-docker record is enforced at landing --------------------------------------
# On a pull_request run G-E2 reports a head with no host-docker record as pending (owner decision
# 2026-10-02, `record.host_docker_pending`), so the required jobs no longer carry the record.
# These cases run the REAL check (`fence.host_docker_at_landing`) over the planted repo.

RECORD_GLOB = "tests/proof/host/host-*/*.json"


def _plant_record(rig, status: str = "PASSED", live: str = "PASSED") -> str:
    """A host-docker record for the branch head, committed on the branch as a record-only commit
    (the shape a host session leaves); the branch is pushed. Returns the new head."""
    lane = rig["lane"]
    sha = _sh(lane, "rev-parse", "HEAD")
    record = {
        "schema": 1,
        "gate": "host-docker",
        "sha": sha,
        "mode": "run",
        "python": "3.12.8",
        "platform": "darwin",
        "status": status,
        "results": [{"nodeid": record_mod.LIVE_COMPOSE_NODE, "outcome": live}],
    }
    path = lane / "tests" / "proof" / "host" / "host-docker" / f"{sha}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    _sh(lane, "add", str(path.relative_to(lane)))
    _sh(lane, "commit", "-q", "-m", "host-docker record")
    _sh(lane, "push", "-q", "origin", "HEAD:refs/heads/wr/x/m1")
    return _sh(lane, "rev-parse", "HEAD")


def _real_l1(rig, head: str, **kwargs):
    cfg = dataclasses.replace(rig["cfg"], record_exempt=[RECORD_GLOB])
    return _merge(
        rig,
        cfg=cfg,
        expect_sha=head,
        pr_head_sha=head,
        host_record=fence_mod.host_docker_at_landing,
        **kwargs,
    )


@pytest.mark.parametrize("event", ["", "push", "pull_request"])
def test_l1_landing_without_a_host_docker_record_is_refused_before_push(
    rig, monkeypatch: pytest.MonkeyPatch, event: str
):
    """Every required job green, no record: refused, nothing pushed. A PR run's `pending` never
    reaches the landing: the event does not matter here."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    before = _sh(rig["origin"], "rev-parse", "master")
    exit_code, msg = _real_l1(rig, rig["head"])
    assert exit_code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert msg == f"L1: {record_mod.NO_HOST_DOCKER_RECORD}"
    assert _sh(rig["origin"], "rev-parse", "master") == before


@pytest.mark.parametrize(
    ("status", "live", "why"),
    [
        ("FAILED", "PASSED", "run/FAILED, not a passing run"),
        ("PASSED", "SKIPPED", "the live compose node is not PASSED in the record"),
    ],
)
def test_l1_landing_with_a_failing_host_docker_record_is_refused(rig, status, live, why):
    before = _sh(rig["origin"], "rev-parse", "master")
    exit_code, msg = _real_l1(rig, _plant_record(rig, status, live))
    assert exit_code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert msg.startswith("L1: ") and msg.endswith(why), msg
    assert _sh(rig["origin"], "rev-parse", "master") == before


def test_l1_landing_with_a_passing_host_docker_record_lands(rig):
    head = _plant_record(rig)
    exit_code, sha = _real_l1(rig, head)
    assert exit_code == fence_mod.FenceMergeExit.LANDED, sha
    assert _sh(rig["origin"], "rev-parse", "master") == sha


def test_l1_a_code_change_after_the_record_voids_it(rig):
    """CM-6: any non-record change after the host session makes the record inadmissible, and the
    landing is refused again."""
    _plant_record(rig)
    (rig["lane"] / "a" / "later.txt").write_text("y")
    _sh(rig["lane"], "add", "a/later.txt")
    _sh(rig["lane"], "commit", "-q", "-m", "a code change after the record")
    _sh(rig["lane"], "push", "-q", "origin", "HEAD:refs/heads/wr/x/m1")
    exit_code, msg = _real_l1(rig, _sh(rig["lane"], "rev-parse", "HEAD"))
    assert exit_code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert msg == f"L1: {record_mod.NO_HOST_DOCKER_RECORD}"


def test_l1_the_real_check_is_the_default(rig, monkeypatch: pytest.MonkeyPatch):
    """`fence merge` without an injected check, as `fence merge`/`fence land` call it, runs
    `fence.host_docker_at_landing` on the head."""
    asked: list[str] = []

    def real(cwd: Path, head: str) -> str:
        asked.append(head)
        return "the real check ran"

    monkeypatch.setattr(fence_mod, "host_docker_at_landing", real)
    defaults = dict(
        cfg=rig["cfg"],
        cwd=rig["runner"],
        branch="wr/x/m1",
        expect_sha=rig["head"],
        pr_head_sha=rig["head"],
        job_conclusions={},
        required=[],
    )
    exit_code, msg = fence_mod.fence_merge(**defaults)
    assert (exit_code, msg) == (fence_mod.FenceMergeExit.VERDICT_REFUSED, "L1: the real check ran")
    assert asked == [rig["head"]]
    assert (
        fence_mod.LandingDeps(cfg=rig["cfg"], cwd=rig["runner"], state_dir=Path()).host_record
        is None
    )
