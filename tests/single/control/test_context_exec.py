"""R1: `Context.exec(argv, ...)` and `Context.remaining()` (decision record change 1b).

A command that outlives its timeout is killed with its children and returns a timed-out result;
10 MB of output stays within the cap; a long poll holds one artifact slot; the environment is
the child's own plus overrides; `credentials=` is refused; a stopped run stops the command."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from trestle.child.context import RuntimeContext
from trestle.plugin import ExecResult


def _make(tmp_path: Path, seconds: float = 60) -> RuntimeContext:
    evidence = tmp_path / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        work=tmp_path / "work",
        evidence=evidence,
        deadline=datetime.now(UTC) + timedelta(seconds=seconds),
        events_path=evidence / "events.ndjson",
    )


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "evidence" / "events.ndjson"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_ok_command_captures_output_and_attaches_it(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    code = "import sys; print('hi'); print('e', file=sys.stderr)"
    result = ctx.exec([sys.executable, "-c", code])
    assert isinstance(result, ExecResult)
    assert result.ok and result.returncode == 0
    assert not result.timed_out and not result.cancelled
    assert result.stdout == "hi\n" and result.stderr == "e\n"
    assert result.artifact
    stored = (tmp_path / "evidence" / "artifacts" / result.artifact).read_text("utf-8")
    assert "--- stdout ---\nhi\n" in stored and "--- stderr ---\ne\n" in stored
    logs = [e["payload"]["message"] for e in _events(tmp_path) if e["kind"] == "log"]
    assert any(m.startswith("exec ") and "exit 0" in m for m in logs)


def test_timeout_kills_the_command_and_its_children(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    pidfile = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(999)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(999)\n"
    )
    started = time.monotonic()
    result = ctx.exec([sys.executable, "-c", script], timeout=1.0)
    assert time.monotonic() - started < 1.0 + 4.0  # the timeout plus the kill grace, with slack
    assert result.timed_out and result.returncode is None and not result.ok
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild)


def test_ten_megabytes_of_output_stay_within_the_cap(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    cap = 64 * 1024
    result = ctx.exec(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * (10 * 1024 * 1024))"],
        max_output_bytes=cap,
    )
    assert result.ok
    assert result.output_bytes == 10 * 1024 * 1024
    assert len(result.stdout) == cap and result.truncated
    size = (tmp_path / "evidence" / "artifacts" / result.artifact).stat().st_size
    assert size < cap + 1024


def test_a_long_poll_holds_one_artifact_slot(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    handles = [ctx.exec([sys.executable, "-c", f"print({i})"]).artifact for i in range(25)]
    assert all(handles) and len(set(handles)) == 25  # every version stays fetchable
    assert ctx._artifact_count == 1
    other = ctx.exec([sys.executable, "-c", "print('x')"], output="other.log")
    assert other.artifact and ctx._artifact_count == 2
    none = ctx.exec([sys.executable, "-c", "print('x')"], output=None)
    assert none.artifact == "" and ctx._artifact_count == 2


def test_environment_is_inherited_with_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXEC_INHERITED", "kept")
    monkeypatch.setenv("EXEC_REMOVED", "gone")
    ctx = _make(tmp_path)
    code = (
        "import os; print(os.environ.get('EXEC_INHERITED'), os.environ.get('EXEC_SET'), "
        "os.environ.get('EXEC_REMOVED'))"
    )
    result = ctx.exec([sys.executable, "-c", code], env={"EXEC_SET": "added", "EXEC_REMOVED": None})
    assert result.stdout.split() == ["kept", "added", "None"]


def test_input_cwd_and_exit_code(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    code = "import os, sys; print(sys.stdin.read().upper(), os.getcwd()); sys.exit(3)"
    result = ctx.exec([sys.executable, "-c", code], input="abc", cwd=tmp_path)
    assert result.returncode == 3 and not result.ok and not result.timed_out
    word, cwd = result.stdout.split()
    assert word == "ABC" and Path(cwd).resolve() == tmp_path.resolve()


def test_timeout_defaults_to_and_never_exceeds_the_time_left(tmp_path: Path) -> None:
    ctx = _make(tmp_path, seconds=1.0)
    assert 0 < ctx.remaining() <= 1.0
    started = time.monotonic()
    result = ctx.exec([sys.executable, "-c", "import time; time.sleep(30)"], timeout=60)
    assert result.timed_out and time.monotonic() - started < 1.0 + 4.0
    assert ctx.remaining() == 0.0
    late = ctx.exec([sys.executable, "-c", "print('never')"])
    assert late.timed_out and late.duration_s == 0.0 and late.artifact == ""


def test_a_stopped_run_stops_the_command(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    (tmp_path / "work" / "cancel.flag").write_text("1")
    result = ctx.exec([sys.executable, "-c", "import time; time.sleep(30)"])
    assert result.cancelled and not result.timed_out and result.returncode is None


def test_credentials_are_reserved_and_shell_strings_refused(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    with pytest.raises(NotImplementedError, match="reserved"):
        ctx.exec(["true"], credentials=["gh"])
    with pytest.raises(TypeError, match="argv"):
        ctx.exec("echo hi")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="argv"):
        ctx.exec([])
