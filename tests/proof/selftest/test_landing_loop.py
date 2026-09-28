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


# --- P3: up to date / out-of-band push --------------------------------------


def test_p3_out_of_band_push_returns_uncounted_and_escalates(rig):
    """A push made outside the loop (the only other way `origin/master` can
    move, since the loop is the single writer) defeats R3 and is returned
    "master moved outside fence merge" — residual 13: not counted toward
    `LANDING_RETURNS_MAX`, but escalated immediately (root CM-3 P3/P6)."""
    _mark_ready(rig)
    # an out-of-band push directly to origin/master, bypassing the loop
    # entirely (the only way it can move, since the loop is the sole writer)
    _sh(rig["master"], "fetch", "-q", "origin")
    _sh(rig["master"], "reset", "-q", "--hard", "origin/master")
    (rig["master"] / "a" / "outside.txt").write_text("z")
    _sh(rig["master"], "add", "a/outside.txt")
    _sh(rig["master"], "commit", "-q", "-m", "pushed outside the loop")
    _sh(rig["master"], "push", "-q", "origin", "HEAD:refs/heads/master")

    outcome = fence_mod.attempt_landing("M1", _deps(rig))
    assert outcome == "master moved outside fence merge"

    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["returns"] == 0  # residual 13: uncounted
    assert data["state"] == "ready"  # not blocked either
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert "master moved outside fence merge" in log  # but escalated


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


def test_p4_checkpoint_first_lapses_after_landing_max(rig):
    """Checkpoint-first holds every other landing once `ckpt_rebases` hits
    `LANDING_RETURNS_MAX`, but lapses — escalating "checkpoint-first
    lapsed" and resuming plain FIFO — once `LANDING_MAX` passes after the
    checkpoint's most recent "checkpoint rebased" return with no re-mark
    (root CM-3 P4, K11K-18)."""
    # M1 was marked READY first (earlier `marked_at`); J0 follows.  Plain
    # FIFO would land M1 first, so J0's hold below is proof of
    # checkpoint-first overriding FIFO, not a coincidence of ordering.
    _mark_ready(rig, "M1", marked_at=1.0)
    _mark_ready(rig, "J0", marked_at=5.0)
    t = {"now": 0.0}
    clock = lambda: t["now"]  # noqa: E731
    for _ in range(fence_mod.LANDING_RETURNS_MAX):
        outcome = fence_mod.attempt_landing(
            "J0", _deps(rig, preflight="checkpoint_rebased", clock=clock)
        )
        assert outcome == "checkpoint rebased: roles 1-2 must re-run"

    # checkpoint-first: J0 holds M1's landing despite M1's earlier marked_at
    assert fence_mod.next_fifo_entry(rig["state_dir"], clock=clock) == "J0"

    t["now"] += fence_mod.LANDING_MAX + 1  # no re-mark of J0 in the meantime
    assert fence_mod.next_fifo_entry(rig["state_dir"], clock=clock) == "M1"
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert "checkpoint-first lapsed" in log


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


def test_p5_ci_never_starts_in_loop_returns_ci_wait_exceeded(rig):
    """Within one landing attempt (step 6's CI wait, as opposed to the
    pre-READY `ci-status --wait` case above), CI that never starts is
    returned "CI wait exceeded" — counted and escalated immediately
    (root CM-3 P5/P6)."""
    _mark_ready(rig)
    outcome = fence_mod.attempt_landing("M1", _deps(rig, ci_wait_result=8))
    assert outcome == "CI wait exceeded"
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["returns"] == 1
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert "CI wait exceeded" in log


def test_p5_ckpt_job_never_concludes_exits_8_ckpt_wait_exceeded(rig):
    """`fence ci-status --ckpt <name> --wait` bounds the checkpoint job's
    own wait separately from the branch form, and names its own reason on
    timeout: "ckpt wait exceeded" (root CM-3 P5)."""
    clock = {"t": 0.0}
    rc = fence_mod.ci_status(
        rig["runner"],
        ckpt="J0",
        wait=True,
        conclusions_reader=lambda sha, cwd: None,
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        ci_wait_max=10,
        state_dir=rig["state_dir"],
    )
    assert rc == 8
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert "ckpt wait exceeded" in log


def test_p5_hung_local_check_killed_at_host_run_max(rig):
    """A hung local check (ruff/mypy/etc, as opposed to a HOST-run step) is
    bounded and killed too, returned under its own name so it is
    distinguishable from "HOST run timed out" while sharing the same
    counted/escalates treatment (root CM-3 P5/P6)."""
    assert fence_mod.HOST_RUN_MAX > 0  # the bound this case is killed at
    _mark_ready(rig)
    outcome = fence_mod.attempt_landing("M1", _deps(rig, preflight="local_check_timeout"))
    assert outcome == "local check timed out"
    data = json.loads(fence_mod._ready_path(rig["state_dir"], "M1").read_text())
    assert data["returns"] == 1
    assert data["state"] == "ready"  # one timeout alone does not yet block
    log = (rig["state_dir"] / "wr-escalations.log").read_text()
    assert "local check timed out" in log


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


def test_p8_restart_at_every_step_boundary(rig, monkeypatch):
    """The loop writes its progress record at every step boundary (root
    CM-3 P8), not just once — checked by spying on `write_progress` through
    one full landing pass, and through a pass that returns early partway,
    which must stop recording boundaries at the step it actually reached."""
    seen: list[str] = []
    orig = fence_mod.write_progress

    def spy(state_dir, merge_id, step, clock=None):
        seen.append(step)
        orig(state_dir, merge_id, step, clock=clock)

    monkeypatch.setattr(fence_mod, "write_progress", spy)

    _mark_ready(rig, "M1", marked_at=1.0)
    outcome = fence_mod.attempt_landing("M1", _deps(rig))
    assert outcome == "landed"
    assert seen == ["step-0", "step-1", "step-2", "step-4", "step-5", "step-6", "step-7"]

    seen.clear()
    _mark_ready(rig, "M2", marked_at=2.0)
    outcome2 = fence_mod.attempt_landing("M2", _deps(rig, preflight="rebase_conflict"))
    assert outcome2 == "rebase conflict"
    # a restart of this attempt would resume from "step-2": no later
    # boundary was ever reached or recorded.
    assert seen == ["step-0", "step-1", "step-2"]


def test_p8_restart_after_rebase_push_compares_landing_sha(rig):
    """Once `landing_sha` is set (a prior, killed loop's rebase was already
    pushed), a restart compares the PR head against `landing_sha`, never
    the entry's original `head_sha` — so it never returns the loop's own
    rebase-push as "head moved during landing" (root CM-3 P8, K11K-17)."""
    _mark_ready(rig, "M1", marked_at=1.0)

    # advance master with a commit the rebase will incorporate
    (rig["master"] / "a" / "master2.txt").write_text("m2")
    _sh(rig["master"], "add", "a/master2.txt")
    _sh(rig["master"], "commit", "-q", "-m", "master moves")
    _sh(rig["master"], "push", "-q", "origin", "HEAD:refs/heads/master")

    # simulate the killed loop's own already-pushed rebase: a real rebase
    # of the lane branch onto the new master, pushed under the same ref
    _sh(rig["lane"], "fetch", "-q", "origin")
    _sh(rig["lane"], "rebase", "-q", "origin/master")
    _sh(rig["lane"], "push", "-q", "-f", "origin", "HEAD:refs/heads/wr/x/m1")
    rebased_sha = _sh(rig["lane"], "rev-parse", "HEAD")
    assert rebased_sha != rig["head"]

    entry_path = fence_mod._ready_path(rig["state_dir"], "M1")
    data = json.loads(entry_path.read_text())
    data["landing_sha"] = rebased_sha  # P7: written before the push, by the killed loop
    fence_mod._atomic_write(entry_path, data)

    # restart: the PR head (as GitHub now reports it) is the rebased sha
    outcome = fence_mod.attempt_landing("M1", _deps(rig, pr_head_sha=rebased_sha))
    assert outcome == "landed"  # not "head moved during landing"


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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_p9_killed_loop_leaves_no_child(tmp_path):
    """The executor kills the *loop's process group*, not just its pid, so
    no `fence merge`/git child survives a stalled-loop kill (root CM-3 P9,
    K11K-14) — proven here with a real spawned-and-killed process group,
    not only the unit-level `progress_is_stale` check above."""
    import signal
    import subprocess as sp
    import time

    marker = tmp_path / "child.pid"
    script = tmp_path / "fake_loop.py"
    script.write_text(
        "import subprocess, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    proc = sp.Popen(["python3", str(script)], preexec_fn=os.setsid)
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not (marker.exists() and marker.read_text().strip()):
            time.sleep(0.05)
        child_pid = int(marker.read_text().strip())
        assert _pid_alive(child_pid)

        # the executor's stall-kill: the whole process group, one signal.
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
        proc.wait(timeout=5)

        deadline = time.time() + 5
        while time.time() < deadline and _pid_alive(child_pid):
            time.sleep(0.05)
        assert not _pid_alive(child_pid), "child outlived the killed loop process group"
    finally:
        for pid in (getattr(proc, "pid", None),):
            if pid is None:
                continue
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


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
