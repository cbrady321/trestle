"""Poll/select reactor for child capture."""

from __future__ import annotations

import codecs
import json
import os
import select
import signal
import subprocess
from pathlib import Path
from types import FrameType
from typing import IO, cast

from trestle.common import redact
from trestle.common.fsutil import atomic_write, atomic_write_json
from trestle.common.limits import CaptureLimits, capture_limits


class Stopped(BaseException):
    """SIGTERM reached the wrapper: stop where it stands, write what was captured, exit. The
    supervisor is the only thing that stops a run (B2-C10); the wrapper keeps no timer. The
    reactor raises it again, after its writes, so the caller exits as a stopped process."""


# Once the wrapper is writing what it captured, a second SIGTERM must not interrupt the write.
_flushing = False


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    if not _flushing:
        raise Stopped


def install_stop_handler() -> None:
    signal.signal(signal.SIGTERM, _on_sigterm)


class _Pipe:
    """One child pipe read by file descriptor. `os.read` returns what is waiting; a text stream's
    `read(n)` waits for `n` characters, and what it had taken in would be lost to a stop."""

    def __init__(self, stream: IO[str]) -> None:
        self.fd = stream.fileno()
        self.eof = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read_some(self) -> str:
        data = os.read(self.fd, 4096)
        if not data:
            self.eof = True
        return self._decoder.decode(data, final=self.eof)

    def read_rest(self) -> str:
        """Read to end of file (blocks while any process holds the other end open)."""
        parts: list[str] = []
        while not self.eof:
            parts.append(self.read_some())
        return "".join(parts)

    def read_available(self) -> str:
        """Read what is already waiting, without blocking."""
        parts: list[str] = []
        while not self.eof and select.select([self.fd], [], [], 0)[0]:
            parts.append(self.read_some())
        return "".join(parts)


def run_reactor(
    proc: subprocess.Popen[str],
    *,
    console_dir: Path,
    report_path: Path,
    limits: CaptureLimits | None = None,
    scrubber: redact.Scrubber | None = None,
) -> dict[str, object]:
    """Capture the child's console into `console_dir` and write the wrapper report.

    Every console write applies `scrubber` (MC-CORE-13): the run's declared secret values and the
    host roots. When it is not given the wrapper builds one from its own environment, where the
    conductor put the values for the child, and from the run directory `console_dir` sits in.
    """
    global _flushing
    _flushing = False
    caps = limits or capture_limits()
    scrub = scrubber or redact.Scrubber(
        secrets=redact.env_strings(), roots=redact.run_roots(console_dir.parent.parent)
    )
    console_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = console_dir / "stdout.log"
    stderr_path = console_dir / "stderr.log"

    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    stdout_bytes = 0
    stderr_bytes = 0
    stdout_suppressed = 0
    stderr_suppressed = 0
    markers: list[dict[str, object]] = []

    stopped = False
    pipes: dict[str, _Pipe | None] = {
        "stdout": _Pipe(cast(IO[str], proc.stdout)) if proc.stdout is not None else None,
        "stderr": _Pipe(cast(IO[str], proc.stderr)) if proc.stderr is not None else None,
    }

    def take(name: str, chunk: str) -> None:
        nonlocal stdout_bytes, stdout_suppressed, stderr_bytes, stderr_suppressed
        if not chunk:
            return
        if name == "stdout":
            stdout_bytes, stdout_suppressed = _accumulate(
                stdout_buf, chunk, stdout_bytes, stdout_suppressed, caps.max_console_bytes
            )
        else:
            stderr_bytes, stderr_suppressed = _accumulate(
                stderr_buf, chunk, stderr_bytes, stderr_suppressed, caps.max_console_bytes
            )

    try:
        while proc.poll() is None:
            live = {name: pipe for name, pipe in pipes.items() if pipe is not None and not pipe.eof}
            if not live:
                select.select([], [], [], 0.1)
                continue
            ready, _, _ = select.select([p.fd for p in live.values()], [], [], 0.1)
            for name, pipe in live.items():
                if pipe.fd in ready:
                    take(name, pipe.read_some())
        for name, opened in pipes.items():
            if opened is not None:
                take(name, opened.read_rest())
    except Stopped:
        # A bounded flush: what is already waiting, never a read to end of file, since a
        # descendant that outlived the stop may hold the pipes open.
        stopped = True
        _flushing = True
        for name, opened in pipes.items():
            if opened is not None:
                take(name, opened.read_available())
    _flushing = True

    _write_console(stdout_path, stdout_buf, caps.max_console_bytes, scrub, bool(stdout_suppressed))
    _write_console(stderr_path, stderr_buf, caps.max_console_bytes, scrub, bool(stderr_suppressed))

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

    # A stopped wrapper does not wait for a child that may still be alive.
    exit_code = proc.poll() if stopped else proc.wait()
    if exit_code is None:
        exit_code = 128 + signal.SIGTERM
    if exit_code == 0:
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
    if stopped:
        raise Stopped  # what was captured is on disk; the wrapper exits as a stopped process
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


def _write_console(
    path: Path,
    chunks: list[str],
    limit: int,
    scrubber: redact.Scrubber = redact.NO_SCRUB,
    capped: bool = False,
) -> None:
    # scrubbed as one text, before it is elided, so no chunk boundary can split a secret; a capture
    # that hit its byte cap may end inside one, so its tail is scrubbed for a split secret too
    joined = "".join(chunks)
    text = scrubber.capped_text(joined) if capped else scrubber.text(joined)
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
