"""`Context.exec` (R1, decision record change 1b): run one command for a plugin, bounded.

argv only, never a shell. The command runs in its own process group with the child's own
environment plus the caller's overrides; on a per-call timeout, or once the run is stopped
(`ctx.cancelled`), the whole group is sent SIGTERM and, after a short grace, SIGKILL. Each
stream is captured up to a byte cap: what passes the cap is counted, not kept. The kept output
is attached under one reused artifact name (T3 replace), so a poll that calls `exec` a thousand
times holds one slot of the run's artifact count, not a thousand.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from trestle.plugin.surface import ExecResult

# Per stream: what `exec` keeps of a command's stdout and of its stderr by default.
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
# From SIGTERM to SIGKILL for a command's group on a timeout or a stop.
KILL_GRACE_S = 2.0
_POLL_S = 0.05


class _Capture:
    """Reads one stream to its end, keeping its first `cap` bytes and counting the rest."""

    def __init__(self, stream: IO[bytes], cap: int) -> None:
        self.kept = bytearray()
        self.total = 0
        self._stream = stream
        self._cap = cap
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while chunk := self._stream.read(65536):
            self.total += len(chunk)
            room = self._cap - len(self.kept)
            if room > 0:
                self.kept += chunk[:room]

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    @property
    def truncated(self) -> bool:
        return self.total > len(self.kept)


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | str | None,
    env: Mapping[str, str | None] | None,
    input: str | bytes | None,
    max_output_bytes: int,
    stopped: Callable[[], bool],
) -> tuple[ExecResult, bytes]:
    """Run `argv` and return its result and the bytes to keep as its output artifact."""
    if isinstance(argv, (str, bytes)) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("exec takes argv: a non-empty list of strings, never a shell string")
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must not be negative")
    child_env = dict(os.environ)
    for key, value in (env or {}).items():
        if value is None:
            child_env.pop(key, None)
        else:
            child_env[key] = value
    data = input.encode("utf-8") if isinstance(input, str) else input
    started = time.monotonic()
    if timeout <= 0:
        return _result(argv, None, True, False, 0.0, None, None), b""
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=child_env,
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    out = _Capture(proc.stdout, max_output_bytes)
    err = _Capture(proc.stderr, max_output_bytes)
    if data is not None:
        assert proc.stdin is not None
        threading.Thread(target=_feed, args=(proc.stdin, data), daemon=True).start()
    timed_out = cancelled = False
    limit = started + timeout
    while proc.poll() is None:
        if time.monotonic() >= limit:
            timed_out = True
        elif stopped():
            cancelled = True
        if timed_out or cancelled:
            _kill_group(proc)
            break
        time.sleep(_POLL_S)
    # the group is gone (or the command exited); a grandchild that kept a pipe open is not waited
    # for past the grace
    out.join(KILL_GRACE_S)
    err.join(KILL_GRACE_S)
    duration = time.monotonic() - started
    returncode = None if (timed_out or cancelled) else proc.returncode
    result = _result(argv, returncode, timed_out, cancelled, duration, out, err)
    return result, _artifact_bytes(argv, result, out, err)


def _feed(stdin: IO[bytes], data: bytes) -> None:
    try:
        stdin.write(data)
    except OSError:  # the command exited without reading it all
        pass
    finally:
        try:
            stdin.close()
        except OSError:
            pass


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM to the command's whole process group; after the grace, SIGKILL to whatever of the
    group is left, the leader included."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def _result(
    argv: Sequence[str],
    returncode: int | None,
    timed_out: bool,
    cancelled: bool,
    duration: float,
    out: _Capture | None,
    err: _Capture | None,
) -> ExecResult:
    return ExecResult(
        argv=tuple(argv),
        returncode=returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        duration_s=round(duration, 3),
        stdout=bytes(out.kept).decode("utf-8", errors="replace") if out else "",
        stderr=bytes(err.kept).decode("utf-8", errors="replace") if err else "",
        output_bytes=(out.total if out else 0) + (err.total if err else 0),
        truncated=bool((out and out.truncated) or (err and err.truncated)),
        artifact="",
    )


def _artifact_bytes(argv: Sequence[str], result: ExecResult, out: _Capture, err: _Capture) -> bytes:
    if result.timed_out:
        status = "timed out"
    elif result.cancelled:
        status = "stopped"
    else:
        status = f"exit {result.returncode}"
    head = f"$ {' '.join(argv)}\n# {status} in {result.duration_s}s\n"
    parts = [head.encode("utf-8"), b"--- stdout ---\n", bytes(out.kept)]
    if out.truncated:
        parts.append(f"\n# {out.total - len(out.kept)} more stdout bytes not kept\n".encode())
    parts += [b"\n--- stderr ---\n", bytes(err.kept)]
    if err.truncated:
        parts.append(f"\n# {err.total - len(err.kept)} more stderr bytes not kept\n".encode())
    return b"".join(parts)
