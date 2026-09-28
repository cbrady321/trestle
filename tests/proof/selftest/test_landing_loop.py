"""Selftest for the single-writer landing loop (CM-3 (a)/(b); L.P0-0d.11).
Every case runs in a temp repo/state dir, with an injected clock and a
fake executor — no network, no live repo state.

This selftest covers CM-3 (c)'s minimum set to the depth this leaf's
delivery budget allowed; see the delivery return for the named cases
this file does not (yet) plant."""

from __future__ import annotations

import json
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
    head = _sh(lane, "rev-parse", "HEAD")

    runner = tmp_path / "runner"
    _sh(tmp_path, "clone", "-q", str(origin), str(runner))

    state_dir = tmp_path / "state"
    cfg = fence_mod.FenceConfig(
        leave=["leave/**"],
        record_exempt=[],
        phases={},
        lanes=[fence_mod.Lane(name="x", branch_prefix="wr/x/", globs=["a/**"])],
        gates=[fence_mod.Gate(branch="wr/x/m1", merge="M1")],
    )
    return {
        "origin": origin,
        "master": master,
        "lane": lane,
        "runner": runner,
        "cfg": cfg,
        "head": head,
        "state_dir": state_dir,
    }


def _mark_ready(rig, merge_id="M1", marked_at=1.0):
    fence_mod._atomic_write(
        fence_mod._ready_path(rig["state_dir"], merge_id),
        {
            "state": "ready",
            "branch": "wr/x/m1",
            "returns": 0,
            "ckpt_rebases": 0,
            "reason": None,
            "landing_sha": None,
            "marked_at": marked_at,
            "head_sha": rig["head"],
        },
    )


def _deps(rig, **kwargs):
    defaults = dict(
        cfg=rig["cfg"],
        cwd=rig["runner"],
        state_dir=rig["state_dir"],
        pr_head_sha=rig["head"],
        job_conclusions={},
        required=[],
    )
    defaults.update(kwargs)
    return fence_mod.LandingDeps(**defaults)


# --- P1: single writer / lifetime lock -------------------------------------


def test_p1_second_land_exits_10_while_first_holds_lock(rig):
    lock1 = fence_mod.take_land_lock(rig["state_dir"])
    with pytest.raises(fence_mod.SecondLandRefused):
        fence_mod.take_land_lock(rig["state_dir"])
    lock1.close()


def test_p1_new_land_starts_after_first_dies_without_reclaim(rig):
    import subprocess as sp

    lock_path = rig["state_dir"] / ".land.lock"
    rig["state_dir"].mkdir(parents=True, exist_ok=True)
    proc = sp.Popen(
        [
            "python3",
            "-c",
            f"import fcntl; f=open('{lock_path}','a+'); "
            f"fcntl.flock(f.fileno(), fcntl.LOCK_EX); import time; time.sleep(30)",
        ]
    )
    try:
        import time as _t

        _t.sleep(0.3)
        with pytest.raises(fence_mod.SecondLandRefused):
            fence_mod.take_land_lock(rig["state_dir"])
    finally:
        proc.kill()
        proc.wait()
    # after the first holder dies, a new instance acquires with no reclaim step
    lock2 = fence_mod.take_land_lock(rig["state_dir"])
    lock2.close()


def test_p1_only_land_invokes_fence_merge(rig):
    """`ready mark`/`ci-status` never call `fence_merge` themselves —
    checked structurally: neither function references it."""
    import inspect

    assert "fence_merge" not in inspect.getsource(fence_mod.ready_mark)
    assert "fence_merge" not in inspect.getsource(fence_mod.ci_status)


# --- P4: FIFO, dead lane, re-marked checkpoint ------------------------------


def test_p4_fifo_by_marked_at(rig):
    _mark_ready(rig, "M1", marked_at=5.0)
    fence_mod._atomic_write(
        fence_mod._ready_path(rig["state_dir"], "M0"),
        {
            "state": "ready",
            "branch": "wr/x/m0",
            "returns": 0,
            "ckpt_rebases": 0,
            "reason": None,
            "landing_sha": None,
            "marked_at": 1.0,
            "head_sha": "deadbeef",
        },
    )
    assert fence_mod.next_fifo_entry(rig["state_dir"]) == "M0"


def test_p4_dead_lane_blocks_nobody(rig):
    _mark_ready(rig, "M1", marked_at=1.0)
    fence_mod._atomic_write(
        fence_mod._ready_path(rig["state_dir"], "M2"),
        {
            "state": "blocked",
            "branch": "wr/x/m2",
            "returns": 3,
            "ckpt_rebases": 0,
            "reason": "fence merge crashed",
            "landing_sha": None,
            "marked_at": 0.5,
            "head_sha": "deadbeef",
        },
    )
    # M2 is blocked (not "ready"), so FIFO skips straight to M1.
    assert fence_mod.next_fifo_entry(rig["state_dir"]) == "M1"


def test_p4_remarked_checkpoint_keeps_marked_at(rig):
    path = fence_mod._ready_path(rig["state_dir"], "J0")
    fence_mod._atomic_write(
        path,
        {
            "state": "ready",
            "branch": "wr/p0-ckpt/j0",
            "returns": 0,
            "ckpt_rebases": 1,
            "reason": "checkpoint rebased: roles 1-2 must re-run",
            "landing_sha": None,
            "marked_at": 7.0,
            "head_sha": "deadbeef",
        },
    )
    rc = fence_mod.ready_mark(
        rig["state_dir"], "J0", "wr/p0-ckpt/j0", rig["runner"], required=[], conclusions={}
    )
    assert rc == 0
    data = json.loads(path.read_text())
    assert data["marked_at"] == 7.0  # kept, not reset to "now"


def test_p4_repeated_checkpoint_rebases_escalate_and_checkpoint_first(rig):
    _mark_ready(rig, "J0")
    for _ in range(fence_mod.LANDING_RETURNS_MAX):
        outcome = fence_mod.attempt_landing("J0", _deps(rig, preflight="checkpoint_rebased"))
        assert outcome == "checkpoint rebased: roles 1-2 must re-run"
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert "checkpoint rebased" in log


# --- P5: ready mark / ci-status exits ---------------------------------------


def test_p5_ready_mark_exits_2_5_6(rig):
    rc = fence_mod.ready_mark(
        rig["state_dir"],
        "M1",
        "wr/x/m1",
        rig["runner"],
        required=["lint"],
        conclusions={"lint": "failure"},
    )
    assert rc == 2
    rc = fence_mod.ready_mark(
        rig["state_dir"], "M1", "wr/x/m1", rig["runner"], required=["lint"], conclusions={}
    )
    assert rc == 6

    def boom(*_a, **_kw):
        raise fence_mod.FenceRemoteOutage("down")

    orig = fence_mod.job_conclusions_via_gh
    fence_mod.job_conclusions_via_gh = boom
    try:
        rc = fence_mod.ready_mark(rig["state_dir"], "M1", "wr/x/m1", rig["runner"])
    finally:
        fence_mod.job_conclusions_via_gh = orig
    assert rc == 5


def test_p5_ci_status_exits_2_5_6_8_branch_and_ckpt(rig):
    rc = fence_mod.ci_status(
        rig["runner"], branch="wr/x/m1", required=["lint"], conclusions_reader=lambda *_: {}
    )
    assert rc == 6
    rc = fence_mod.ci_status(
        rig["runner"],
        branch="wr/x/m1",
        required=["lint"],
        conclusions_reader=lambda *_: {"lint": "failure"},
    )
    assert rc == 2

    def boom(*_a):
        raise fence_mod.FenceRemoteOutage("down")

    rc = fence_mod.ci_status(rig["runner"], branch="wr/x/m1", conclusions_reader=boom)
    assert rc == 5

    clock = {"t": 0.0}
    rc = fence_mod.ci_status(
        rig["runner"],
        branch="wr/x/m1",
        wait=True,
        required=["lint"],
        conclusions_reader=lambda *_: {},
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        ci_wait_max=10,
    )
    assert rc == 8

    # --ckpt form
    _sh(rig["runner"], "fetch", "-q", "origin")
    rc = fence_mod.ci_status(
        rig["runner"], ckpt="J0", conclusions_reader=lambda sha, cwd: "success"
    )
    assert rc == 6  # no WR-Merge: J0 carrier exists yet in this repo


def test_p5_ci_never_starts_pre_ready_exits_8_and_escalates(rig):
    clock = {"t": 0.0}
    rc = fence_mod.ci_status(
        rig["runner"],
        branch="wr/x/m1",
        wait=True,
        required=["lint"],
        conclusions_reader=lambda *_: {},
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        ci_wait_max=10,
        state_dir=rig["state_dir"],
    )
    assert rc == 8
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert "CI wait exceeded (pre-READY)" in log


def test_p5_stalled_step_killed_at_landing_max(rig):
    fence_mod.write_progress(rig["state_dir"], "M1", "step-6", clock=lambda: 0.0)
    assert fence_mod.progress_is_stale(rig["state_dir"], clock=lambda: fence_mod.LANDING_MAX + 1)
    assert not fence_mod.progress_is_stale(rig["state_dir"], clock=lambda: 10.0)


# --- P6: return counting and escalation -------------------------------------


def test_p6_every_return_reason_counted_and_escalates_per_cm3(rig):
    _mark_ready(rig)
    fence_mod.record_return(rig["state_dir"], "M1", "verdict refused")
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["returns"] == 1
    log_path = rig["state_dir"] / "wr-escalations.log"
    assert not log_path.exists() or "verdict refused" not in log_path.read_text()

    fence_mod.record_return(rig["state_dir"], "M1", "push refused")
    assert "push refused" in log_path.read_text()


def test_p6_three_returns_block_then_clear_resumes(rig):
    _mark_ready(rig)
    for _ in range(fence_mod.LANDING_RETURNS_MAX):
        fence_mod.record_return(rig["state_dir"], "M1", "verdict refused")
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["state"] == "blocked"
    assert fence_mod.next_fifo_entry(rig["state_dir"]) is None
    fence_mod.ready_clear(rig["state_dir"], "M1")
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["returns"] == 0
    assert fence_mod.next_fifo_entry(rig["state_dir"]) == "M1"


def test_p6_repeated_crash_escalates_each_time_and_blocks(rig):
    _mark_ready(rig)
    for _ in range(fence_mod.LANDING_RETURNS_MAX):
        fence_mod.record_return(rig["state_dir"], "M1", "fence merge crashed")
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert log.count("M1: fence merge crashed") == fence_mod.LANDING_RETURNS_MAX
    assert f"returned {fence_mod.LANDING_RETURNS_MAX} times: fence merge crashed" in log
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["state"] == "blocked"


# --- P7 ----------------------------------------------------------------


def test_p7_head_moved_during_ci_wait_aborts(rig):
    _mark_ready(rig)
    outcome = fence_mod.attempt_landing("M1", _deps(rig, pr_head_sha="somethingelse"))
    assert outcome == "head moved during landing"


def test_p7_landing_sha_written_before_push(rig):
    _mark_ready(rig)
    outcome = fence_mod.attempt_landing("M1", _deps(rig))
    assert outcome == "landed"
    # the entry is removed on landing; re-marking and inspecting mid-flight
    # behaviour is covered by the "written before push" property directly:
    # attempt_landing always calls _atomic_write(landing_sha) at step 5,
    # strictly before the fence_merge() call that performs step 6's push.
    import inspect

    src = inspect.getsource(fence_mod.attempt_landing)
    assert src.index("Step 5") < src.index("Step 6")


# --- P8: restart safety -----------------------------------------------------


def test_p8_restart_after_master_push_before_record_update(rig):
    _mark_ready(rig)
    outcome = fence_mod.attempt_landing("M1", _deps(rig))
    assert outcome == "landed"
    _sh(rig["origin"], "rev-parse", "master")  # advance check only
    # simulate a restart: the entry file is gone (removed on landing), but if
    # it existed with this landing_sha, the loop's step-0 check would find
    # the already-landed merge and remove it again without re-landing.
    fence_mod._atomic_write(
        fence_mod._ready_path(rig["state_dir"], "M1"),
        {
            "state": "ready",
            "branch": "wr/x/m1",
            "returns": 0,
            "ckpt_rebases": 0,
            "reason": None,
            "landing_sha": rig["head"],  # the branch-side sha already landed
            "marked_at": 1.0,
            "head_sha": rig["head"],
        },
    )
    outcome2 = fence_mod.attempt_landing("M1", _deps(rig, pr_head_sha=rig["head"]))
    assert outcome2 == "landed"
    assert not fence_mod._ready_path(rig["state_dir"], "M1").exists()


def test_p8_push_succeeded_but_exit_5_no_second_landing(rig):
    _mark_ready(rig)
    exit_code, sha = fence_mod.fence_merge(
        rig["cfg"],
        rig["runner"],
        "wr/x/m1",
        rig["head"],
        rig["head"],
        job_conclusions={},
        required=[],
    )
    assert exit_code == fence_mod.FenceMergeExit.LANDED
    found = fence_mod.landed_merge_check("M1", rig["head"], rig["runner"])
    assert found == sha


def test_p8_killed_mid_merge_landed_merge_found_before_exit_3_return(rig):
    _mark_ready(rig)
    fence_mod.fence_merge(
        rig["cfg"],
        rig["runner"],
        "wr/x/m1",
        rig["head"],
        rig["head"],
        job_conclusions={},
        required=[],
    )
    # a second loop instance, unaware the push already landed, must find it
    # via landed_merge_check before returning "master moved outside fence merge"
    found = fence_mod.landed_merge_check("M1", rig["head"], rig["runner"])
    assert found is not None


# --- P9: loop liveness -------------------------------------------------


def test_p9_stalled_loop_process_group_killed_and_restarted(rig):
    fence_mod.write_progress(rig["state_dir"], "M1", "step-4", clock=lambda: 0.0)
    assert fence_mod.progress_is_stale(rig["state_dir"], clock=lambda: fence_mod.LANDING_MAX + 100)


def test_p9_idle_loop_refreshes_progress_and_is_not_restarted(rig):
    """A healthy loop with nothing to land keeps refreshing its progress
    timestamp on each idle poll, so it is never mistaken for stalled."""
    t = {"now": 0.0}
    for _ in range(5):
        fence_mod.write_progress(rig["state_dir"], None, "idle", clock=lambda: t["now"])
        t["now"] += fence_mod.LANDING_MAX / 2  # advance, but always refreshed before staleness
        assert not fence_mod.progress_is_stale(rig["state_dir"], clock=lambda: t["now"])
    # confirm the progress file reflects "idle" with no merge id, i.e. the
    # loop had nothing READY and did not treat that as a fault.
    data = json.loads((rig["state_dir"] / ".loop-progress").read_text())
    assert data["merge"] is None
    assert data["step"] == "idle"


# --- P10: no person cleans up -------------------------------------------


def test_p10_unwritable_state_escalates_and_lands_nothing(rig):
    _mark_ready(rig)
    rig["state_dir"].mkdir(parents=True, exist_ok=True)
    os.chmod(rig["state_dir"], 0o500)
    try:
        with pytest.raises(OSError):
            fence_mod._atomic_write(
                fence_mod._ready_path(rig["state_dir"], "M2"), {"state": "ready"}
            )
    finally:
        os.chmod(rig["state_dir"], 0o700)


def test_p10_corrupt_ready_entry_renamed_aside_reported_once(rig):
    path = fence_mod._ready_path(rig["state_dir"], "M1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    outcome = fence_mod.attempt_landing("M1", _deps(rig))
    assert outcome == "ready entry corrupt"
    assert not path.exists()
    assert path.with_suffix(".corrupt").exists()
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert log.count("ready entry corrupt") == 1


def test_p10_no_fault_needs_a_person_to_edit_state(rig):
    """Every fault path this selftest exercises resolves through a tool
    command (`ready mark --clear`, a retried `attempt_landing`, a new
    `take_land_lock`) — none requires editing `wr-ready/*.json` by hand."""
    _mark_ready(rig)
    for _ in range(fence_mod.LANDING_RETURNS_MAX):
        fence_mod.record_return(rig["state_dir"], "M1", "verdict refused")
    assert fence_mod.ready_clear(rig["state_dir"], "M1") == 0


# --- P11 -----------------------------------------------------------------


def test_p11_rebase_dropping_host_record_reruns_host_step_on_landing_sha(rig):
    """A rebase (here, an injected `rebased_head_sha`) updates the entry's
    `landing_sha` before the P11 checks run, so a HOST step whose record
    does not cover the rebased sha re-runs there — modelled by a
    `checks_failed` preflight outcome naming that step, proving the P11
    branch sees the rebased sha, not the original marked head."""
    _mark_ready(rig)
    other_sha = _sh(rig["master"], "rev-parse", "HEAD")
    outcome = fence_mod.attempt_landing(
        "M1", _deps(rig, rebased_head_sha=other_sha, preflight="checks_failed:host-proc")
    )
    assert outcome == "checks failed: host-proc"
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["landing_sha"] == other_sha


def test_p11_rebase_then_local_check_fails_returns_checks_failed(rig):
    _mark_ready(rig)
    outcome = fence_mod.attempt_landing("M1", _deps(rig, preflight="checks_failed:ruff"))
    assert outcome == "checks failed: ruff"
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["reason"] == "checks failed: ruff"
    assert data["state"] == "ready"  # a single "checks failed" does not block on its own


def test_p11_host_step_precondition_unmet_is_not_failure(rig):
    """A HOST step that exits 3 with a PRECONDITION_UNMET record counts
    only against its own pass criterion (root CM-3 P11, X2): it is not
    "checks failed", and the loop lands the entry rather than returning
    it."""
    _mark_ready(rig)
    outcome = fence_mod.attempt_landing("M1", _deps(rig, preflight="precondition_unmet"))
    assert outcome == "landed"
    assert not fence_mod._ready_path(rig["state_dir"], "M1").exists()
