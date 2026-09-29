"""L.NW-2.2 (strict preflight) — and, from L.NW-2.10, the gate `run` / `pin-images` cases: the
host-docker gate core over the absolute-path `fake_docker.py` shim. No real engine, no pull."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import threading
from pathlib import Path

import jsonschema
import pytest

from tests.proof import fence as fence_mod
from tests.proof import tolerances
from tests.proof.host import host_lock
from tests.proof.host import record as record_mod
from tests.proof.host.docker_gate import __main__ as gate_main
from tests.proof.host.docker_gate import fake_docker
from tests.proof.host.docker_gate import preflight as preflight_mod

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
