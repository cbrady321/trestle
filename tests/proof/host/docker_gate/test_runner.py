"""L.NW-2.2 (strict preflight) — and, from L.NW-2.10, the gate `run` / `pin-images` cases: the
host-docker gate core over the absolute-path `fake_docker.py` shim. No real engine, no pull."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import jsonschema
import pytest

from tests.proof import fence as fence_mod
from tests.proof import tolerances
from tests.proof.host import host_lock
from tests.proof.host import record as record_mod
from tests.proof.host.docker_gate import __main__ as gate_main
from tests.proof.host.docker_gate import fake_docker, inventory, select
from tests.proof.host.docker_gate import preflight as preflight_mod
from tests.proof.host.docker_gate import run as run_mod

FAKE = str(Path(fake_docker.__file__).resolve())
ENDPOINT = "unix:///fake/desktop-linux.sock"
DIGEST = "sha256:" + "a" * 64
PINNED = {"alpine": {"ref": "alpine:3.20", "digest": DIGEST}}
UNPINNED = {"alpine": {"ref": "alpine:3.20", "digest": ""}}
NO_PULL = pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
IMAGE = {
    "id": "sha256:img1",
    "repository": "alpine",
    "tag": "3.20",
    "repo_digests": [f"alpine@{DIGEST}"],
}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


class Rig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "master")
        (self.repo / "code.txt").write_text("x")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "root")
        self.sha = _git(self.repo, "rev-parse", "HEAD")
        self.records = tmp_path / "records"
        self.lock = tmp_path / "host.lock"
        self.state = tmp_path / "state.json"
        self.log = tmp_path / "log.jsonl"
        monkeypatch.setenv(fake_docker.STATE_ENV, str(self.state))
        monkeypatch.setenv(fake_docker.LOG_ENV, str(self.log))
        monkeypatch.delenv(host_lock.HELD_ENV, raising=False)
        # the `default` context's absent socket must never be used
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
        monkeypatch.setenv("DOCKER_CONTEXT", "default")
        self.monkeypatch = monkeypatch
        self.engine()

    def engine(self, *, reachable=True, images=None, endpoint=ENDPOINT, **extra) -> None:
        fake_docker.write_state(
            self.state,
            reachable=reachable,
            images=[IMAGE] if images is None else images,
            endpoint=endpoint,
            **extra,
        )

    def strict(self, images=PINNED, **kwargs) -> int:
        kwargs.setdefault("docker_path", FAKE)
        return preflight_mod.strict_preflight(
            cwd=self.repo,
            record_dir=self.records,
            images=images,
            pip_runner=lambda *a: None,
            lock_path=self.lock,
            **kwargs,
        )

    def record(self) -> dict:
        return json.loads((self.records / f"{self.sha}.json").read_text())

    def calls(self) -> list[dict]:
        return fake_docker.read_log(self.log)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def _lock_is_free(path: Path) -> bool:
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return True


def _run_in_thread(fn) -> tuple[threading.Thread, threading.Event, dict]:
    done, out = threading.Event(), {}

    def target() -> None:
        try:
            out["rc"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            out["exc"] = exc
        done.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, done, out


# -- WR-PROOF-5:b-cached-images-only-no-pull -----------------------------------------------------


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_single_occupancy_lock(rig):
    """With `TRESTLE_HOST_LOCK_HELD` unset a second `--strict` waits while another holder has the
    (test-injected) lock, then runs under it (setting the variable for its children)."""
    with open(rig.lock, "a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        thread, done, out = _run_in_thread(rig.strict)
        assert not done.wait(tolerances.SETTLE_S)  # blocked on the lock: nothing ran yet
        assert rig.calls() == []
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        assert done.wait(tolerances.JOIN_WAIT_S), "second --strict never got the lock"
    thread.join(tolerances.JOIN_WAIT_S)
    assert out == {"rc": 0}
    assert {c["env"]["TRESTLE_HOST_LOCK_HELD"] for c in rig.calls()} == {"1"}
    assert host_lock.HELD_ENV not in os.environ  # released
    assert _lock_is_free(rig.lock)


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_held_lock_env_not_reacquired(rig):
    """With the variable set (an outer caller holds the lock) `--strict` acquires nothing: it
    completes although the lock file is exclusively held by someone else."""
    rig.monkeypatch.setenv(host_lock.HELD_ENV, "1")
    with open(rig.lock, "a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        thread, done, out = _run_in_thread(rig.strict)
        assert done.wait(tolerances.JOIN_WAIT_S), "--strict tried to re-acquire the held lock"
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    thread.join(tolerances.JOIN_WAIT_S)
    assert out == {"rc": 0}
    assert os.environ[host_lock.HELD_ENV] == "1"  # still the caller's


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_missing_image_records_precondition_unmet(rig):
    rig.engine(images=[])
    assert rig.strict() == 3
    rec = rig.record()
    assert rec["status"] == "PRECONDITION_UNMET" and rec["mode"] == "preflight"
    assert rec["images"] == [f"alpine: alpine@{DIGEST} not present"]
    assert rec["engine"]["reachable_before"] is True


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_unpinned_role_records_precondition_unmet(rig):
    # never cached, cached without a RepoDigests entry (docker load), cached but not yet pinned
    for images, reason in (
        ([], "not cached"),
        ([{**IMAGE, "repo_digests": []}], "no RepoDigest"),
        ([IMAGE], "cached; run pin-images"),
    ):
        rig.engine(images=images)
        assert rig.strict(images=UNPINNED) == 3
        assert rig.record()["images"] == [f"alpine: unpinned ({reason})"]
    # a repo-digest of some OTHER repository is not the ref's digest
    rig.engine(images=[{**IMAGE, "repo_digests": [f"busybox@{DIGEST}"]}])
    assert rig.strict(images=UNPINNED) == 3
    assert rig.record()["images"] == ["alpine: unpinned (no RepoDigest)"]


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_engine_unreachable_records_precondition_unmet(rig):
    rig.engine(reachable=False)
    assert rig.strict() == 3
    rec = rig.record()
    assert rec["status"] == "PRECONDITION_UNMET"
    assert rec["engine"]["reachable_before"] is False and rec["engine"]["reachable_after"] is False
    assert "engine unreachable" in rec["engine"]["reason"] and ENDPOINT in rec["engine"]["reason"]
    # the images were never inspected while the engine was down
    assert not [c for c in rig.calls() if c["args"][:2] == ["image", "inspect"]]

    # no CLI at all, and an unreadable context endpoint, are named too
    assert rig.strict(docker_path="", which=lambda _n: None) == 3
    assert rig.record()["engine"]["reason"] == "docker CLI not resolved"
    rig.engine(endpoint="")
    assert rig.strict() == 3
    assert "endpoint of context desktop-linux unreadable" in rig.record()["engine"]["reason"]


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_strict_preflight_exit_3_writes_record(rig):
    # exit 0 writes no record
    assert rig.strict() == 0
    assert not rig.records.exists() or not list(rig.records.glob("*.json"))
    # any gap: exit 3 and a record for the head naming it; through the CLI too
    rig.engine(images=[])
    assert rig.strict() == 3
    assert (rig.records / f"{rig.sha}.json").exists()
    assert rig.record()["sha"] == rig.sha
    with rig.monkeypatch.context() as mp:
        mp.setattr(preflight_mod, "strict_preflight", lambda: preflight_mod.STRICT_UNMET_EXIT)
        assert gate_main.main(["preflight", "--strict"]) == 3


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_pull_attempt_fails_closed(rig):
    """No pull argv ever reaches the shim, whatever the gap; and if one did, it is refused and
    the shim's log shows it (the guard the assertion above reads)."""
    for images, state_images in (
        (PINNED, []),
        (UNPINNED, []),
        (UNPINNED, [IMAGE]),
        (PINNED, [IMAGE]),
    ):
        rig.engine(images=state_images)
        rig.strict(images=images)
    rig.engine(reachable=False)
    rig.strict()
    seen = rig.calls()
    assert seen and not [c for c in seen if "pull" in c["args"]]
    assert not [c for c in seen if c["args"][:2] == ["compose", "up"]]
    # the shim itself refuses a pull (fails closed) and records it
    before = len(seen)
    done = subprocess.run(
        [FAKE, "pull", "alpine:3.20"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 1 and "pull refused" in done.stderr
    assert [c["args"] for c in rig.calls()[before:]] == [["pull", "alpine:3.20"]]


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_record_validates_against_mc27_schema(rig):
    schema = json.loads((Path(record_mod.__file__).parent / "record.schema.json").read_text())
    rig.engine(images=[])
    assert rig.strict() == 3
    rec = rig.record()
    record_mod.validate_schema(rec)
    jsonschema.validate(rec, schema)
    assert set(rec["engine"]) >= {
        "cli_path",
        "endpoint",
        "server_version",
        "reachable_before",
        "reachable_after",
    }
    assert rec["engine"]["server_version"] == "29.8.0" and rec["engine"]["cli_path"] == FAKE
    # P0's report-mode record (a message string in `engine`) stays valid under the same schema
    report = preflight_mod.run_preflight(
        cwd=rig.repo, docker_path=FAKE, images=PINNED, info_runner=lambda argv: _Done()
    )
    record_mod.validate_schema(report)
    jsonschema.validate(report, schema)


class _Done:
    returncode, stdout, stderr = 0, "3.12.8\ndarwin\n", ""


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_endpoint_from_desktop_linux_context_recorded(rig):
    rig.engine(images=[])
    assert rig.strict() == 3
    calls = rig.calls()
    inspect = [c for c in calls if c["args"][:2] == ["context", "inspect"]]
    assert len(inspect) == 1  # read once per invocation
    assert inspect[0]["args"][2] == "desktop-linux" and inspect[0]["host"] is None
    for call in calls:
        if call["args"][:2] != ["context", "inspect"]:
            assert call["host"] == ENDPOINT  # the desktop-linux endpoint, on every argv
        # neither the ambient default socket nor the default context leaks through
        assert call["env"]["DOCKER_HOST"] is None and call["env"]["DOCKER_CONTEXT"] is None
    assert "/var/run/docker.sock" not in json.dumps(calls)
    assert rig.record()["engine"]["endpoint"] == ENDPOINT


@pytest.mark.proves(
    "WR-PROOF-5", "WR-PROOF-5:b-cached-images-only-no-pull", "B", "B", "LOGIC", "CI"
)
def test_strict_preflight_hung_killed_at_host_run_max(rig, capsys):
    """A docker call hung past the bound is killed with its process group, the lock is freed,
    "HOST run timed out" is reported and no record is written."""
    assert fence_mod.HOST_RUN_MAX > 0  # the production bound this planted case stands in for
    rig.monkeypatch.setenv(fake_docker.MODE_ENV, "hang")
    pid = None
    try:
        with pytest.raises(host_lock.HostRunTimedOut, match="HOST run timed out"):
            rig.strict(host_run_max=tolerances.SETTLE_LONG_S)
        calls = rig.calls()
        assert calls, "the hung docker call never started"
        pid = calls[0]["pid"]
        for _ in range(int(tolerances.PROC_WAIT_S / tolerances.POLL_FINE_S)):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            threading.Event().wait(tolerances.POLL_FINE_S)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert not rig.records.exists() or not list(rig.records.glob("*.json"))
    assert host_lock.HELD_ENV not in os.environ
    assert _lock_is_free(rig.lock)

    # the CLI reports it and exits non-zero (never 3: a timeout is not a precondition)
    def hung() -> int:
        raise host_lock.HostRunTimedOut("HOST run timed out")

    with rig.monkeypatch.context() as mp:
        mp.setattr(preflight_mod, "strict_preflight", hung)
        assert gate_main.main(["preflight", "--strict"]) == 1
    assert "HOST run timed out" in capsys.readouterr().out


# -- L.NW-2.10: run, select, pin-images ----------------------------------------------------------

REPO_ROOT = Path(fake_docker.__file__).resolve().parents[4]
GATE_RUN = pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:gate-records-diff", "B", "B", "LOGIC", "CI")
ROLE_REFS = {
    "alpine": ("alpine", "3.20", "a"),
    "postgres": ("postgres", "16-alpine", "b"),
    "http_support": ("nginx", "1.27-alpine", "c"),
}
ALL_PINNED = {
    role: {"ref": f"{repo}:{tag}", "digest": "sha256:" + ch * 64}
    for role, (repo, tag, ch) in ROLE_REFS.items()
}
ALL_CACHED = [
    {
        "id": f"sha256:id{ch}",
        "repository": repo,
        "tag": tag,
        "repo_digests": [f"{repo}@sha256:" + ch * 64],
    }
    for repo, tag, ch in ROLE_REFS.values()
]
SELECTOR = "trwr-r_0abc123def-db"
FIXTURE = {inventory.FIXTURE_LABEL: "ref-compose"}


def _pytest_proc(argv, rc=0):
    return subprocess.CompletedProcess(argv, rc, "", "")


def _write_audit(env, outcomes=None):
    outcomes = outcomes or {"planted::t": "passed"}
    Path(env["TRESTLE_AUDIT_OUT"]).write_text(
        json.dumps(
            {
                "nodes": [{"nodeid": n, "labels": []} for n in outcomes],
                "outcomes": {n: {"outcome": o} for n, o in outcomes.items()},
            }
        )
    )


def _gate(rig, **kwargs):
    """`run.run` over the fake engine, with everything that reaches outside injected."""
    kwargs.setdefault("docker_path", FAKE)
    kwargs.setdefault("images", ALL_PINNED)
    return run_mod.run(
        cwd=rig.repo,
        record_dir=rig.records,
        pip_runner=lambda *a: None,
        info_runner=lambda argv: _Done(),
        lock_path=rig.lock,
        **kwargs,
    )


def _new_sha(rig) -> None:
    _git(rig.repo, "commit", "-q", "--allow-empty", "-m", "next")
    rig.sha = _git(rig.repo, "rev-parse", "HEAD")


def _plant(rig, name: str, body: str) -> Path:
    path = rig.repo / "planted" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return path


def _pytest_command(*paths: Path, plugins=("select",)) -> list[str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "tests.proof.plugin"]
    cmd += ["-p", "tests.proof.audit_plugin", "-o", "addopts="]
    for name in plugins:
        cmd += ["-p", f"tests.proof.host.docker_gate.{name}"]
    return [*cmd, *map(str, paths)]


def _repo_pythonpath() -> dict[str, str]:
    existing = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": os.pathsep.join(p for p in (str(REPO_ROOT), existing) if p)}


PLANTED = """
import pytest

@pytest.mark.docker_host
def test_a_marked(): pass

def test_b_unmarked(): pass

def test_c_unmarked(): pass

@pytest.mark.slow
def test_d_slow(): pass

@pytest.mark.docker_host
def test_skips():
    pytest.skip("nothing to see")

@pytest.mark.docker_host
@pytest.mark.gated_on("OQ-1")
def test_gated_skip():
    pytest.skip("registered to skip")
"""


def _results(rig) -> dict[str, str]:
    return {r["nodeid"].split("::")[-1]: r["outcome"] for r in rig.record()["results"]}


@GATE_RUN
def test_skip_inside_gate_is_failed(rig):
    rig.engine(images=ALL_CACHED)
    planted = _plant(rig, "test_planted.py", PLANTED)
    rc = _gate(
        rig,
        command=_pytest_command(planted),
        extra_env={**_repo_pythonpath(), "PYTEST_ADDOPTS": ""},
    )
    assert rc == 0
    rec = rig.record()
    assert rec["status"] == "FAILED"  # the skip is not a pass
    assert _results(rig) == {
        "test_a_marked": "PASSED",
        "test_skips": "FAILED",
        "test_gated_skip": "SKIPPED",  # a node registered gated_on/na may skip (CM-6)
    }
    record_mod.validate_schema(rec)


@GATE_RUN
def test_no_host_node_collected_without_gate_env(rig):
    """CSC-9 re-assertion: outside the gate the root plugin's one hook DESELECTS `docker_host`
    and `host_only` nodes (never skips them), in every testpath of the root session."""
    env = {**os.environ, "TRESTLE_HOST_GATE": ""}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-c",
            "pyproject.toml",
            "--rootdir",
            ".",
            "-m",
            "host_only or docker_host",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not [ln for ln in done.stdout.splitlines() if "::" in ln], done.stdout[-2000:]
    # and a planted docker_host node is deselected, not skipped
    planted = _plant(rig, "test_planted.py", PLANTED)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tests.proof.plugin",
            "-o",
            "addopts=",
            str(planted),
        ],
        cwd=rig.repo,
        env={**env, **_repo_pythonpath()},
        capture_output=True,
        text=True,
        check=False,
    )
    assert "skipped" not in done.stdout, done.stdout
    assert "3 passed" in done.stdout and "3 deselected" in done.stdout, done.stdout


def _collected(rig, planted: Path, env_extra: dict[str, str]) -> set[str]:
    done = subprocess.run(
        [*_pytest_command(planted)[:-1], "--collect-only", str(planted)],
        cwd=rig.repo,
        env={**os.environ, "TRESTLE_HOST_GATE": "docker", **_repo_pythonpath(), **env_extra},
        capture_output=True,
        text=True,
        check=False,
    )
    return {ln.split("::")[-1] for ln in done.stdout.splitlines() if "::" in ln}


@GATE_RUN
def test_select_union_with_marker_expr(rig):
    rig.engine(images=ALL_CACHED)
    planted = _plant(rig, "test_planted.py", PLANTED)
    b = "planted/test_planted.py::test_b_unmarked"
    docker_host = {"test_a_marked", "test_skips", "test_gated_skip"}
    # the run set is every docker_host node ...
    assert _collected(rig, planted, {}) == docker_host
    # ... union every selected id, whatever the -m expression says (an unmarked id is kept)
    only_selected = {select.SELECT_ENV: json.dumps([b])}
    assert _collected(rig, planted, only_selected) == docker_host | {"test_b_unmarked"}
    # ... union whatever the -m expression matches
    both = {**only_selected, select.MARKEXPR_ENV: "slow"}
    assert _collected(rig, planted, both) == docker_host | {"test_b_unmarked", "test_d_slow"}
    # a file id selects its nodes; a function id selects all its parametrizations
    file_sel = {select.SELECT_ENV: json.dumps(["planted/test_planted.py"])}
    assert len(_collected(rig, planted, file_sel)) == 6

    # `run --select <unmarked id> -- -m docker_host`: the runner hands `-m` to select.py, never to
    # pytest, and the real pytest run keeps that node and every docker_host node
    seen = {}

    def spy(argv, env):
        seen.update(env)
        return _pytest_proc(argv)

    rc = _gate(rig, select_ids=[b], pytest_args=["-m", "docker_host", "-x"], pytest_runner=spy)
    assert rc == 0
    assert seen[select.MARKEXPR_ENV] == "docker_host"
    assert json.loads(seen[select.SELECT_ENV]) == [b]
    assert run_mod.split_marker_args(["-m", "a", "-x", "--markexpr=b", "-mc"]) == (
        "((a) or (b)) or (c)",
        ["-x"],
    )
    _new_sha(rig)
    rc = _gate(
        rig,
        select_ids=[b],
        pytest_args=["-m", "docker_host"],
        command=_pytest_command(planted),
        extra_env=_repo_pythonpath(),
    )
    assert rc == 0
    assert set(_results(rig)) == docker_host | {"test_b_unmarked"}


@GATE_RUN
def test_one_run_per_sha_refused(rig):
    rig.engine(images=ALL_CACHED)
    rig.records.mkdir()
    existing = rig.records / f"{rig.sha}.json"
    existing.write_text('{"sentinel": true}')
    rc = _gate(rig, pytest_runner=lambda argv, env: pytest.fail("ran despite a record"))
    assert rc != 0
    assert existing.read_text() == '{"sentinel": true}'  # nothing written or overwritten
    assert list(rig.records.iterdir()) == [existing]
    assert rig.calls() == []  # no docker call at all


@GATE_RUN
def test_nested_strict_preflight_never_reacquires_lock(rig, monkeypatch):
    rig.engine(images=ALL_CACHED)
    acquisitions = []
    real_flock = fcntl.flock

    def spy(fd, op):
        if op & fcntl.LOCK_EX:
            acquisitions.append(op)
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", spy)

    def fake_pytest(argv, env):
        assert env["TRESTLE_HOST_LOCK_HELD"] == "1"  # every child runs under the hold
        _write_audit(env)
        return _pytest_proc(argv)

    # HELD unset: the lock is taken exactly once (strict preflight nests inside, taking nothing)
    assert _gate(rig, pytest_runner=fake_pytest) == 0
    assert len(acquisitions) == 1
    assert host_lock.HELD_ENV not in os.environ and rig.record()["status"] == "PASSED"

    # a caller already holding it (variable set): zero acquisitions, although the file is locked
    _new_sha(rig)
    acquisitions.clear()
    monkeypatch.setenv(host_lock.HELD_ENV, "1")
    with open(rig.lock, "a+") as other:
        real_flock(other.fileno(), fcntl.LOCK_EX)
        thread, done, out = _run_in_thread(lambda: _gate(rig, pytest_runner=fake_pytest))
        assert done.wait(tolerances.JOIN_WAIT_S), "run tried to re-acquire the held lock"
        real_flock(other.fileno(), fcntl.LOCK_UN)
    thread.join(tolerances.JOIN_WAIT_S)
    assert out == {"rc": 0} and acquisitions == []

    # a planted re-acquisition inside `preflight --strict` waits on the held lock, times out, fails
    _new_sha(rig)
    monkeypatch.delenv(host_lock.HELD_ENV)

    def reacquiring_strict(**kwargs):
        with open(rig.lock, "a+") as fh:
            for _ in range(int(tolerances.SETTLE_S / tolerances.POLL_FINE_S)):
                try:
                    real_flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return 0
                except BlockingIOError:
                    threading.Event().wait(tolerances.POLL_FINE_S)
        raise host_lock.HostRunTimedOut("HOST run timed out")

    monkeypatch.setattr(preflight_mod, "strict_preflight", reacquiring_strict)
    with pytest.raises(host_lock.HostRunTimedOut):
        _gate(rig, pytest_runner=fake_pytest)
    assert not (rig.records / f"{rig.sha}.json").exists()


@GATE_RUN
def test_housekeeping_removes_only_attributable_residue(rig):
    base_containers = [
        {"id": "c-old", "names": ["found-old"], "image": "alpine", "state": "running",
         "labels": FIXTURE},
    ]  # fmt: skip
    rig.engine(images=ALL_CACHED, containers=base_containers, volumes=[], networks=[])
    seen_env = {}

    def fake_pytest(argv, env):
        seen_env.update(env)
        state = fake_docker.read_state(rig.state)
        state["containers"] += [
            {"id": "c1", "names": [SELECTOR], "image": "alpine", "state": "running", "labels": {}},
            {"id": "c2", "names": ["fx"], "image": "alpine", "state": "running", "labels": FIXTURE},
        ]
        state["networks"] += [
            {"id": "n1", "name": "trwr-r_0abc123def-net", "labels": {}},
            {"id": "n2", "name": "fx-net", "labels": FIXTURE},
        ]
        state["volumes"] += [
            {"name": "fx-vol", "labels": FIXTURE},
            {"name": "trwr-r_0abc123def-data", "labels": {}},
        ]
        fake_docker.write_state(rig.state, **state)
        _write_audit(env)
        return _pytest_proc(argv)

    assert _gate(rig, pytest_runner=fake_pytest) == 0
    rec = rig.record()
    assert rec["status"] == "PASSED" and rec["diff"]["unattributed"] == []
    after = fake_docker.read_state(rig.state)
    assert [c["id"] for c in after["containers"]] == ["c-old"]  # the pre-existing one is untouched
    assert after["networks"] == []
    # the selector-named volume is attributable residue but unlabelled: never removed
    assert [v["name"] for v in after["volumes"]] == ["trwr-r_0abc123def-data"]
    removed = {(r["kind"], r["name"]): r["removed"] for r in rec["residue"]}
    assert removed == {
        ("containers", SELECTOR): True,
        ("containers", "fx"): True,
        ("networks", "trwr-r_0abc123def-net"): True,
        ("networks", "fx-net"): True,
        ("volumes", "fx-vol"): True,
        ("volumes", "trwr-r_0abc123def-data"): False,
    }
    housekeeping = [c["args"] for c in rig.calls() if c["args"][0] in ("rm", "network", "volume")]
    housekeeping = [
        a for a in housekeeping if a[:2] != ["network", "ls"] and a[:2] != ["volume", "ls"]
    ]
    assert all("-v" not in a and "--volumes" not in a and "--force" not in a for a in housekeeping)

    # an unattributable stray fails the record (and is never removed)
    _new_sha(rig)

    def stray_pytest(argv, env):
        state = fake_docker.read_state(rig.state)
        state["volumes"] += [{"name": "stray", "labels": {}}]
        fake_docker.write_state(rig.state, **state)
        _write_audit(env)
        return _pytest_proc(argv)

    assert _gate(rig, pytest_runner=stray_pytest) == 0
    assert rig.record()["status"] == "FAILED"
    assert [e["name"] for e in rig.record()["diff"]["unattributed"]] == ["stray"]
    assert "stray" in [v["name"] for v in fake_docker.read_state(rig.state)["volumes"]]


@GATE_RUN
def test_image_env_exported_from_pins(rig):
    rig.engine(images=ALL_CACHED)
    seen = {}

    def spy(argv, env):
        seen.update(env)
        _write_audit(env)
        return _pytest_proc(argv)

    assert _gate(rig, pytest_runner=spy) == 0
    assert seen["TRESTLE_IMAGE_ALPINE"] == "alpine@sha256:" + "a" * 64
    assert seen["TRESTLE_IMAGE_POSTGRES"] == "postgres@sha256:" + "b" * 64
    assert seen["TRESTLE_IMAGE_HTTP_SUPPORT"] == "nginx@sha256:" + "c" * 64
    assert seen["TRESTLE_DOCKER_ENDPOINT"] == ENDPOINT
    assert seen["TRESTLE_HOST_GATE"] == "docker"
    assert "DOCKER_HOST" not in seen and "DOCKER_CONTEXT" not in seen  # endpoint is explicit
    assert rig.record()["images"] == {
        "alpine": "alpine@sha256:" + "a" * 64,
        "postgres": "postgres@sha256:" + "b" * 64,
        "http_support": "nginx@sha256:" + "c" * 64,
    }
    assert rig.record()["engine"]["endpoint"] == ENDPOINT
    assert run_mod.image_env(UNPINNED) == {}  # an unpinned role exports nothing


@GATE_RUN
def test_unmet_preflight_stops_the_run_and_leaves_the_record(rig):
    rig.engine(images=[])
    rc = _gate(rig, pytest_runner=lambda argv, env: pytest.fail("ran on an unmet preflight"))
    assert rc == 3
    assert rig.record()["status"] == "PRECONDITION_UNMET" and rig.record()["mode"] == "preflight"


def _images_file(tmp_path: Path) -> Path:
    path = tmp_path / "images.toml"
    path.write_text(
        "# header line one\n# header line two\n"
        + "".join(
            f'\n[{role}]\nref = "{spec["ref"]}"\ndigest = ""\n' for role, spec in ALL_PINNED.items()
        )
    )
    return path


@GATE_RUN
def test_pin_images_records_repo_digest_inspect_only(rig, tmp_path):
    path = _images_file(tmp_path)
    cached = [
        # a RepoDigests entry of another repository comes first; the image Id is not the digest
        {
            **ALL_CACHED[0],
            "repo_digests": ["busybox@sha256:" + "f" * 64, *ALL_CACHED[0]["repo_digests"]],
        },
        *ALL_CACHED[1:],
    ]
    rig.engine(images=cached)
    rc = run_mod.pin_images(path, docker_path=FAKE, lock_path=rig.lock)
    assert rc == 0
    pinned = preflight_mod.load_images(path)
    assert {r: s["digest"] for r, s in pinned.items()} == {
        r: s["digest"] for r, s in ALL_PINNED.items()
    }
    assert all(
        not s["digest"].endswith("id" + ch)
        for (_, _, ch), s in zip(ROLE_REFS.values(), pinned.values(), strict=True)
    )
    assert path.read_text().startswith("# header line one\n# header line two\n")
    verbs = {tuple(c["args"][:2]) for c in rig.calls()}
    assert verbs == {("context", "inspect"), ("image", "inspect")}  # read-only, no pull
    assert all(c["host"] == ENDPOINT for c in rig.calls() if c["args"][0] == "image")
    # the pinned file now passes the strict preflight
    assert rig.strict(images=pinned) == 0


@GATE_RUN
def test_pin_images_no_repo_digest_stays_unpinned(rig, tmp_path, capsys):
    path = _images_file(tmp_path)
    # alpine restored with `docker load` (no RepoDigests), postgres cached, nginx not cached
    rig.engine(images=[{**ALL_CACHED[0], "repo_digests": []}, ALL_CACHED[1]])
    rc = run_mod.pin_images(path, docker_path=FAKE, lock_path=rig.lock)
    assert rc == 3
    pinned = preflight_mod.load_images(path)
    assert pinned["alpine"]["digest"] == "" and pinned["http_support"]["digest"] == ""
    assert pinned["postgres"]["digest"] == "sha256:" + "b" * 64
    out = capsys.readouterr().out
    assert "no RepoDigest" in out and "not cached" in out
    # the strict preflight names each unpinned role
    assert rig.strict(images=pinned) == 3
    assert rig.record()["images"] == [
        "alpine: unpinned (no RepoDigest)",
        "http_support: unpinned (not cached)",
    ]
    assert not [c for c in rig.calls() if c["args"][0] in ("pull", "run", "create", "start")]


@GATE_RUN
def test_hung_gate_run_killed_at_host_run_max(rig):
    """A planted hung pytest child is killed with its process group at the bound, the host lock is
    free afterwards, "HOST run timed out" is reported and no record is written."""
    rig.engine(images=ALL_CACHED)
    pidfile = rig.tmp / "grandchild.pid"
    script = f"sleep 300 & echo $! > {pidfile}; wait"  # a shell and a backgrounded grandchild
    grandchild = None
    try:
        with pytest.raises(host_lock.HostRunTimedOut, match="HOST run timed out"):
            _gate(rig, command=["sh", "-c", script], host_run_max=tolerances.PROC_WAIT_S)
        assert pidfile.exists()
        grandchild = int(pidfile.read_text())
        for _ in range(int(tolerances.PROC_WAIT_S / tolerances.POLL_FINE_S)):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            threading.Event().wait(tolerances.POLL_FINE_S)
        with pytest.raises(ProcessLookupError):
            os.kill(grandchild, 0)
    finally:
        if grandchild is not None:
            try:
                os.kill(grandchild, signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert not rig.records.exists() or not list(rig.records.glob("*.json"))
    assert host_lock.HELD_ENV not in os.environ and _lock_is_free(rig.lock)
