"""Poll/select reactor for child capture."""

from __future__ import annotations

import json
import select
import subprocess
import time
from pathlib import Path
from typing import IO, cast

from trestle.common.fsutil import atomic_write, atomic_write_json
from trestle.common.limits import CaptureLimits, capture_limits


def run_reactor(
    proc: subprocess.Popen[str],
    *,
    console_dir: Path,
    report_path: Path,
    timeout_s: int,
    limits: CaptureLimits | None = None,
) -> dict[str, object]:
    caps = limits or capture_limits()
    console_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = console_dir / "stdout.log"
    stderr_path = console_dir / "stderr.log"
    deadline = time.monotonic() + timeout_s
    classification = "succeeded"

    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    stdout_bytes = 0
    stderr_bytes = 0
    stdout_suppressed = 0
    stderr_suppressed = 0
    markers: list[dict[str, object]] = []

    while proc.poll() is None:
        if time.monotonic() > deadline:
            proc.kill()
            classification = "timed_out"
            break
        readable: list[IO[str]] = []
        if proc.stdout is not None:
            readable.append(cast(IO[str], proc.stdout))
        if proc.stderr is not None:
            readable.append(cast(IO[str], proc.stderr))
        if not readable:
            time.sleep(0.1)
            continue
        ready, _, _ = select.select(readable, [], [], 0.1)
        for stream in ready:
            if stream is proc.stdout and proc.stdout is not None:
                chunk = proc.stdout.read(4096)
                if chunk:
                    stdout_bytes, stdout_suppressed = _accumulate(
                        stdout_buf,
                        chunk,
                        stdout_bytes,
                        stdout_suppressed,
                        caps.max_console_bytes,
                    )
            elif stream is proc.stderr and proc.stderr is not None:
                chunk = proc.stderr.read(4096)
                if chunk:
                    stderr_bytes, stderr_suppressed = _accumulate(
                        stderr_buf,
                        chunk,
                        stderr_bytes,
                        stderr_suppressed,
                        caps.max_console_bytes,
                    )

    if proc.stdout is not None:
        rest = proc.stdout.read()
        if rest:
            stdout_bytes, stdout_suppressed = _accumulate(
                stdout_buf,
                rest,
                stdout_bytes,
                stdout_suppressed,
                caps.max_console_bytes,
            )
    if proc.stderr is not None:
        rest = proc.stderr.read()
        if rest:
            stderr_bytes, stderr_suppressed = _accumulate(
                stderr_buf,
                rest,
                stderr_bytes,
                stderr_suppressed,
                caps.max_console_bytes,
            )

    _write_console(stdout_path, stdout_buf, caps.max_console_bytes)
    _write_console(stderr_path, stderr_buf, caps.max_console_bytes)

    if stdout_suppressed:
        markers.append(
            {
                "stream": "stdout",
                "limit": "max_console_bytes",
                "bytes_recorded": min(stdout_bytes, caps.max_console_bytes),
                "bytes_suppressed": stdout_suppressed,
            }
        )
    if stderr_suppressed:
        markers.append(
            {
                "stream": "stderr",
                "limit": "max_console_bytes",
                "bytes_recorded": min(stderr_bytes, caps.max_console_bytes),
                "bytes_suppressed": stderr_suppressed,
            }
        )
    if markers:
        atomic_write(
            console_dir / "limits.ndjson",
            ("\n".join(json.dumps(item, separators=(",", ":")) for item in markers) + "\n").encode(
                "utf-8"
            ),
        )

    exit_code = proc.wait()
    if classification == "timed_out":
        pass
    elif exit_code == 0:
        classification = "succeeded"
    elif exit_code == 1:
        classification = "failed"
    else:
        classification = "worker_exit"

    report = {
        "exit_code": exit_code,
        "classification": classification,
        "limits_exceeded": markers,
    }
    atomic_write_json(report_path, report)
    return report


def _accumulate(
    buf: list[str],
    chunk: str,
    recorded: int,
    suppressed: int,
    limit: int,
) -> tuple[int, int]:
    encoded = chunk.encode("utf-8")
    size = len(encoded)
    if recorded + size <= limit:
        buf.append(chunk)
        return recorded + size, suppressed
    if recorded < limit:
        keep = limit - recorded
        buf.append(encoded[:keep].decode("utf-8", errors="replace"))
        recorded = limit
        suppressed += size - keep
    else:
        suppressed += size
    return recorded, suppressed


def _write_console(path: Path, chunks: list[str], limit: int) -> None:
    text = "".join(chunks)
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        atomic_write(path, encoded)
        return
    first = int(limit * 0.25)
    last = limit - first
    head = encoded[:first].decode("utf-8", errors="replace")
    tail = encoded[-last:].decode("utf-8", errors="replace")
    marker = "\n... [console elided] ...\n"
    atomic_write(path, (head + marker + tail).encode("utf-8"))
