"""Self-tests for `tests.proof.ancestry` (L.P0-0b.4; MC-13 ancestry)."""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from pathlib import Path

from tests.proof import ancestry, tolerances

ROOT = Path(__file__).resolve().parents[3]


def _spawn_marked_sleeper(
    seconds: float, marker: str, *, new_session: bool = False
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; time.sleep(float(sys.argv[1]))",
            str(seconds),
            marker,
        ],
        start_new_session=new_session,
    )


def _spawn_unrelated_sleeper(seconds: float) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))", str(seconds)]
    )


def test_planted_unrelated_process_not_attributed() -> None:
    marker = f"trestle-anc-{uuid.uuid4().hex[:12]}"
    root = _spawn_marked_sleeper(2.0, marker, new_session=True)
    other = _spawn_unrelated_sleeper(2.0)
    try:
        time.sleep(tolerances.SETTLE_S)
        snap = ancestry.snapshot()
        root_info = next(p for p in snap if p.pid == root.pid)
        attributed = ancestry.attribute(root_info, snap, marker=marker)
        assert other.pid not in {p.pid for p in attributed}
    finally:
        root.kill()
        other.kill()
        root.wait(timeout=tolerances.PROC_WAIT_S)
        other.wait(timeout=tolerances.PROC_WAIT_S)


def test_reap_leaves_no_marker_process() -> None:
    marker = f"trestle-anc-{uuid.uuid4().hex[:12]}"
    proc = _spawn_marked_sleeper(30.0, marker)
    try:
        time.sleep(tolerances.SETTLE_S)
        before = ancestry.snapshot()
        planted = {p for p in before if marker in p.argv}
        assert len(planted) == 1

        ancestry.reap(planted)
        proc.wait(timeout=tolerances.PROC_WAIT_S)

        deadline = time.monotonic() + 3
        remaining = planted
        while time.monotonic() < deadline:
            after = ancestry.snapshot()
            remaining = ancestry.survivors(planted, after)
            if not remaining:
                break
            time.sleep(tolerances.POLL_S)
        assert not remaining
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=tolerances.PROC_WAIT_S)


def test_start_is_zone_and_locale_free() -> None:
    marker = f"trestle-anc-{uuid.uuid4().hex[:12]}"
    proc = _spawn_marked_sleeper(2.0, marker)
    try:
        first = ancestry.start_time(proc.pid)
        assert first is not None
        assert isinstance(first, int)
        second = ancestry.start_time(proc.pid)
        assert first == second
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)


def test_start_never_parsed_from_ps_text() -> None:
    """`ancestry.py` asks `ps` for exactly `pid=,ppid=,args=` — never a
    start-time column (`lstart`, `etime`, `stime`, ...) whose text would be
    zone- or locale-dependent. Checked against the source lines that
    actually invoke `ps` (not the module docstring, which discusses the
    columns it must avoid by name)."""
    assert ancestry.PS_FIELDS == "pid=,ppid=,args="
    lines = (ROOT / "tests" / "proof" / "ancestry.py").read_text(encoding="utf-8").splitlines()
    ps_call_lines = [i for i, line in enumerate(lines) if '"ps"' in line]
    assert len(ps_call_lines) == 1
    window = "\n".join(lines[ps_call_lines[0] : ps_call_lines[0] + 3])
    assert "PS_FIELDS" in window


def test_survivors_cli_exit_status() -> None:
    marker = f"trestle-anc-{uuid.uuid4().hex[:12]}"
    proc = _spawn_marked_sleeper(3.0, marker)
    try:
        time.sleep(tolerances.SETTLE_S)
        assert ancestry.main(["survivors", "--marker", marker]) == 1

        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)

        deadline = time.monotonic() + 3
        code = 1
        while time.monotonic() < deadline:
            code = ancestry.main(["survivors", "--marker", marker])
            if code == 0:
                break
            time.sleep(tolerances.POLL_S)
        assert code == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=tolerances.PROC_WAIT_S)


def test_no_marker_reports_nothing() -> None:
    assert ancestry.main(["survivors"]) == 0
