"""Toolchain stub executables (L.RB-4.1; MC-B-06, MC-13).

`stub_mise`, `stub_tool` and `fetch_logger` live under `tests/fixtures/stubs/`, are invoked only by
absolute path and are never on PATH under a forbidden name (WR-PROOF-5:stubs-never-on-path).
"""

from __future__ import annotations

import ast
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests.proof import guards, tolerances

ROOT = Path(__file__).resolve().parents[3]
STUBS = ROOT / "tests" / "fixtures" / "stubs"
STUB_MISE = STUBS / "stub_mise.py"
STUB_TOOL = STUBS / "stub_tool.py"
FETCH_LOGGER = STUBS / "fetch_logger.py"
AUTO_DOWNLOAD_OFF = "-Porg.gradle.java.installations.auto-download=false"
DIST = "gradle-8.5-bin"
REFUSED_EXIT = 7


def _run(
    stub: Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: int | None = subprocess.DEVNULL,
) -> subprocess.CompletedProcess[str]:
    # a scrubbed environment: only what the case passes (plus what the interpreter itself needs)
    return subprocess.run(
        [sys.executable, str(stub), *args],
        env={**(env or {})},
        stdin=stdin,
        capture_output=True,
        text=True,
        timeout=tolerances.JOIN_WAIT_S,
        check=False,
    )


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _mise(tmp_path: Path, tools: dict[str, Any], mode: str = "normal") -> dict[str, str]:
    config = tmp_path / "mise.json"
    config.write_text(json.dumps({"mode": mode, "tools": tools}), encoding="utf-8")
    return {"STUB_MISE_CONFIG": str(config), "STUB_MISE_LOG": str(tmp_path / "mise.log")}


def _entry(version: str, path: str, installed: bool = True) -> dict[str, Any]:
    return {
        "version": version,
        "requested_version": version,
        "install_path": path,
        "source": {"type": "mise.toml", "path": "mise.toml"},
        "installed": installed,
        "active": installed,
    }


def test_stub_mise_json_surface(tmp_path: Path) -> None:
    env = _mise(
        tmp_path,
        {
            "java": _entry("21.0.2", "/opt/jdk"),
            "node": [_entry("20.1.0", "/opt/node", installed=False)],
        },
    )
    proc = _run(STUB_MISE, "ls", "--current", "--json", env=env)
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert set(body) == {"java", "node"}
    assert body["java"][0]["installed"] is True
    assert body["java"][0]["install_path"] == "/opt/jdk"
    assert body["node"][0]["installed"] is False  # the gate the resolver reads
    assert set(body["java"][0]) >= {"version", "requested_version", "install_path", "installed"}
    only = json.loads(_run(STUB_MISE, "ls", "--current", "--json", "java", env=env).stdout)
    assert set(only) == {"java"}
    # drift and malformed modes are outside the window the resolver accepts (B3-E3)
    drift = _run(STUB_MISE, "ls", "--current", "--json", env={**env, "STUB_MISE_MODE": "drift"})
    assert isinstance(json.loads(drift.stdout), list)
    bad = _run(STUB_MISE, "ls", "--current", "--json", env={**env, "STUB_MISE_MODE": "malformed"})
    with pytest.raises(json.JSONDecodeError):
        json.loads(bad.stdout)
    # only `ls --current --json` exists: exec, run and install are refused and logged
    for forbidden in (["exec", "--", "python"], ["run", "test"], ["install", "--locked"]):
        proc = _run(STUB_MISE, *forbidden, env=env)
        assert proc.returncode == 2
    calls = _lines(tmp_path / "mise.log")
    assert [c["forbidden"] for c in calls].count(True) == 3
    assert not any(c["forbidden"] for c in calls if c["argv"][:1] == ["ls"])


def test_stub_mise_adopted_interpreter_mode(tmp_path: Path) -> None:
    env = _mise(tmp_path, {"python": _entry("3.0.0", "/nonexistent")}, mode="adopted-interpreter")
    proc = _run(STUB_MISE, "ls", "--current", "--json", env=env)
    assert proc.returncode == 0, proc.stderr
    (entry,) = json.loads(proc.stdout)["python"]
    assert entry["installed"] is True
    assert entry["version"] == platform.python_version()
    # install_path/bin is the directory of the interpreter that ran the stub (the gate's), not a
    # pinned fixture path and not a PATH search result
    assert Path(entry["install_path"], "bin") == Path(sys.executable).parent
    assert entry["install_path"] != "/nonexistent"


def test_stub_tool_logs_argv_env_tty(tmp_path: Path) -> None:
    log = tmp_path / "tool.log"
    env = {"STUB_TOOL_LOG": str(log), "ALLOWED_VAR": "yes", "STUB_TOOL_EXIT": "3"}
    proc = _run(STUB_TOOL, "test", "--flag", "x y", env=env)
    assert proc.returncode == 3
    (rec,) = _lines(log)
    assert rec["argv"] == ["test", "--flag", "x y"]
    assert rec["env"]["ALLOWED_VAR"] == "yes"
    assert (
        "HOME" not in rec["env"] and "PATH" not in rec["env"]
    )  # nothing inherited from the caller
    assert rec["isatty"] == {"stdin": False, "stdout": False, "stderr": False}
    assert (rec["stdin"], rec["stdin_bytes"]) == ("eof", 0)
    # stdin left open but idle is recorded as such, and the stub never blocks on it
    open_proc = subprocess.Popen(
        [sys.executable, str(STUB_TOOL)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert open_proc.wait(timeout=tolerances.JOIN_WAIT_S) == 3
    finally:
        assert open_proc.stdin is not None
        open_proc.stdin.close()
        open_proc.stdout and open_proc.stdout.close()
        open_proc.stderr and open_proc.stderr.close()
    assert _lines(log)[-1]["stdin"] == "idle"


def _wrapper_project(tmp_path: Path) -> Path:
    wrapper = tmp_path / "proj" / "gradle" / "wrapper"
    wrapper.mkdir(parents=True)
    jar = wrapper / "gradle-wrapper.jar"
    jar.write_bytes(b"")
    (wrapper / "gradle-wrapper.properties").write_text(
        f"distributionUrl=https\\://services.gradle.org/distributions/{DIST}.zip\n",
        encoding="utf-8",
    )
    return jar


def _wrapper_run(tmp_path: Path, jar: Path, *extra: str) -> tuple[int, list[dict[str, Any]]]:
    fetch_log = tmp_path / "fetch.log"
    fetch_log.unlink(missing_ok=True)
    env = {
        "STUB_TOOL_LOG": str(tmp_path / "tool.log"),
        "STUB_FETCH_LOG": str(fetch_log),
        "STUB_TOOL_DIST_STORE": str(tmp_path / "store"),
    }
    proc = _run(
        STUB_TOOL,
        "--mode",
        "wrapper-fetch",
        "-classpath",
        str(jar),
        "org.gradle.wrapper.GradleWrapperMain",
        "--no-daemon",
        *extra,
        "build",
        env=env,
    )
    return proc.returncode, _lines(fetch_log)


@pytest.mark.parametrize("kind", ["distribution", "toolchain"])
def test_stub_tool_wrapper_fetch_calls_fetch_logger_unless_disabled(
    tmp_path: Path, kind: str
) -> None:
    jar = _wrapper_project(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    if kind == "distribution":
        # declared distribution absent from the store: the first-run download is attempted
        code, fetched = _wrapper_run(tmp_path, jar, AUTO_DOWNLOAD_OFF)
        assert [f["fetch"] for f in fetched] == ["distribution"]
        assert fetched[0]["target"] == DIST and fetched[0]["refused"] is True
        assert code == 1  # the refused download fails the run
        # present in the store: none, with the auto-download property set
        (store / DIST).mkdir()
        code, fetched = _wrapper_run(tmp_path, jar, AUTO_DOWNLOAD_OFF)
        assert fetched == [] and code == 0
    else:
        (store / DIST).mkdir()  # distribution present, so only the toolchain fetch is in question
        code, fetched = _wrapper_run(tmp_path, jar)  # no auto-download property
        assert [f["fetch"] for f in fetched] == ["toolchain"]
        assert code == 1
        code, fetched = _wrapper_run(tmp_path, jar, AUTO_DOWNLOAD_OFF)
        assert fetched == [] and code == 0
    # a wrapper run also records itself like any other stub_tool run
    assert _lines(tmp_path / "tool.log")[-1]["mode"] == "wrapper-fetch"


def test_fetch_logger_refuses_network_and_logs(tmp_path: Path) -> None:
    log = tmp_path / "fetch.log"
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        server.setblocking(False)
        url = f"http://127.0.0.1:{server.getsockname()[1]}/dist.zip"
        proc = _run(
            FETCH_LOGGER,
            "--fetch",
            "distribution",
            "--target",
            DIST,
            "--url",
            url,
            env={"STUB_FETCH_LOG": str(log)},
        )
        assert proc.returncode == REFUSED_EXIT
        with pytest.raises(BlockingIOError):
            server.accept()  # the logger never connected, though the URL was live
    (rec,) = _lines(log)
    assert rec["fetch"] == "distribution" and rec["url"] == url and rec["refused"] is True
    # refuses with no log configured too, and unknown kinds are a usage error, not a fetch
    assert _run(FETCH_LOGGER, "--fetch", "toolchain").returncode == REFUSED_EXIT
    assert _run(FETCH_LOGGER, "--fetch", "bogus").returncode == 2
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(FETCH_LOGGER.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    assert not imported & {"socket", "urllib", "http", "ssl", "requests", "asyncio", "ftplib"}


@pytest.mark.proves("WR-PROOF-5", "WR-PROOF-5:stubs-never-on-path", "B", "B", "PROC", "CI")
def test_no_forbidden_name_resolvable_on_path() -> None:
    assert guards.find_forbidden_path_binaries() == []  # the session's own PATH probe (MC-13)
    stubs = sorted(STUBS.glob("*.py"))
    assert {p.name for p in stubs} >= {"stub_mise.py", "stub_tool.py", "fetch_logger.py"}
    path_dirs = [Path(p).resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    assert STUBS.resolve() not in path_dirs
    for stub in stubs:
        assert stub.stem not in guards.FORBIDDEN_PATH_BINARIES  # none is named a forbidden binary
        assert shutil.which(stub.name) is None and shutil.which(stub.stem) is None  # never on PATH
        assert os.access(stub, os.X_OK)  # runnable by absolute path
        assert stub.read_text(encoding="utf-8").startswith("#!")
    # non-vacuous: were a stub named after the tool it imitates and put on PATH, the probe finds it
    assert guards.find_forbidden_path_binaries(str(STUBS)) == []
    with tempfile.TemporaryDirectory() as tmp:
        planted = Path(tmp) / "mise"
        shutil.copy(STUB_MISE, planted)
        planted.chmod(0o755)
        assert guards.find_forbidden_path_binaries(tmp) == ["mise"]
