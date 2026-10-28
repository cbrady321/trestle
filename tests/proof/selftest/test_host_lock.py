"""Selftest for the CSC-12 HOST lock (L.P0-0d.9)."""

from __future__ import annotations

import os

import pytest

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


# --- L.P0-0d.9: HOST runs re-point and run in the clean venv only ---------


def _git_repo(path):
    import subprocess

    path.mkdir()
    for args in (["init", "-q", "-b", "master"], ["commit", "-q", "--allow-empty", "-m", "r"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=path,
            check=True,
            capture_output=True,
        )
    return path


def test_repoint_uses_venv_python_pip_with_worktree_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(host_lock, "_translated", lambda: False)
    (tmp_path / "packages" / "trestle-packs").mkdir(parents=True)
    venv = tmp_path / "venv"
    calls = []
    host_lock._repoint_venv(tmp_path, pip_runner=lambda a, c: calls.append((a, c)), venv=venv)
    ((args, cwd),) = calls
    assert args[:6] == [str(venv / "bin" / "python"), "-m", "pip", "install", "--no-deps", "-e"]
    assert args[6] == "." and "-e" in args[7:]
    assert str(tmp_path / "packages" / "trestle-packs") in args
    assert cwd == tmp_path
    assert "pip" not in args[:1]  # never the ambient PATH pip


def test_failed_repoint_raises(tmp_path):
    class R:
        returncode = 1
        stderr = "no index"

    with pytest.raises(host_lock.RepointFailed):
        host_lock._repoint_venv(tmp_path, pip_runner=lambda a, c: R(), venv=tmp_path / "v")


def test_venv_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(host_lock.VENV_ENV, str(tmp_path / "alt"))
    assert host_lock.venv_python() == tmp_path / "alt" / "bin" / "python"


def test_missing_venv_real_repoint_raises_unavailable(tmp_path):
    with pytest.raises(host_lock.VenvUnavailable):
        host_lock._repoint_venv(tmp_path, venv=tmp_path / "absent")


def test_proc_gate_missing_venv_records_precondition_unmet(tmp_path, monkeypatch):
    import json

    from tests.proof.host import proc_gate

    monkeypatch.delenv(host_lock.HELD_ENV, raising=False)
    repo = _git_repo(tmp_path / "r")
    rec_dir = tmp_path / "recs"
    rc = proc_gate.run(cwd=repo, record_dir=rec_dir, venv=tmp_path / "absent")
    assert rc != 0
    (rec,) = [json.loads(p.read_text()) for p in rec_dir.glob("*.json")]
    assert rec["status"] == "PRECONDITION_UNMET"


def test_proc_gate_argv_uses_venv_python_and_records_its_version(tmp_path, monkeypatch):
    monkeypatch.setattr(host_lock, "_translated", lambda: False)
    import json
    import subprocess

    from tests.proof.host import proc_gate

    monkeypatch.delenv(host_lock.HELD_ENV, raising=False)
    repo = _git_repo(tmp_path / "r")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("")
    argvs = []

    real_popen = subprocess.Popen

    class Popen:
        returncode = 0

        def __new__(cls, argv, **kw):
            if argv[0] == "git":
                return real_popen(argv, **kw)
            return super().__new__(cls)

        def __init__(self, argv, **kw):
            argvs.append(argv)

        def communicate(self, timeout=None):
            return ("", "")

    monkeypatch.setattr(subprocess, "Popen", Popen)

    class Info:
        stdout = "3.12.9\ndarwin\n"

    rec_dir = tmp_path / "recs"
    rc = proc_gate.run(
        cwd=repo,
        record_dir=rec_dir,
        pip_runner=lambda a, c: None,
        lock_path=tmp_path / "lock",
        venv=venv,
        info_runner=lambda a: Info(),
        collector=lambda _x: [{"nodeid": "t::a", "host_only": True}],
    )
    assert rc == 0
    assert argvs[0][0] == str(venv / "bin" / "python")
    (rec,) = [json.loads(p.read_text()) for p in rec_dir.glob("*.json")]
    assert (rec["python"], rec["platform"]) == ("3.12.9", "darwin")


def test_venv_command_runs_native_arm64_under_rosetta(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    monkeypatch.setattr(host_lock, "_translated", lambda: True)
    assert host_lock.venv_command(venv) == ["/usr/bin/arch", "-arm64", str(venv / "bin" / "python")]
    monkeypatch.setattr(host_lock, "_translated", lambda: False)
    assert host_lock.venv_command(venv) == [str(venv / "bin" / "python")]


def test_audit_plugin_names_xfail_and_xpass(tmp_path):
    import json
    import subprocess
    import sys
    import textwrap

    (tmp_path / "test_x.py").write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.xfail(strict=True, reason="defect:G-X")
            def test_red():
                assert False

            @pytest.mark.xfail(strict=False, reason="loose")
            def test_loose():
                pass
            """
        )
    )
    out = tmp_path / "audit.json"
    env = dict(os.environ, TRESTLE_AUDIT_OUT=str(out))
    root = str(__import__("pathlib").Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tests.proof.audit_plugin",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            "-c",
            os.devnull,
            str(tmp_path / "test_x.py"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    outcomes = {
        k.split("::")[-1]: v["outcome"] for k, v in json.loads(out.read_text())["outcomes"].items()
    }
    assert outcomes == {"test_red": "xfailed", "test_loose": "xpassed"}
