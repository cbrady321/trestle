#!/usr/bin/env python3
"""A fake tool that logs how it was run (MC-B-06, L.RB-4.1).

Invoked only by absolute path. Default mode records argv, the environment it received, whether
stdin/stdout/stderr are terminals and what stdin holds, then exits `STUB_TOOL_EXIT` (default 0).
The log path is `STUB_TOOL_LOG` (one JSON line per run); without it nothing is written.

`--mode wrapper-fetch` stands for the resolved `java` running the project's wrapper main class
(B3-C14's Gradle rule): `-classpath <gradle/wrapper/gradle-wrapper.jar>
org.gradle.wrapper.GradleWrapperMain --no-daemon ...`. It reproduces the two unprompted fetches of
the JVM wrapper pattern (WR-ENV-16, D-19), each through `fetch_logger` (never a socket):
  (1) `fetch=distribution` whenever the distribution the wrapper declares is absent from the stub's
      store: the name comes from `distributionUrl` in `gradle-wrapper.properties` next to the jar
      (else `STUB_TOOL_DISTRIBUTION`), the store is the directory `STUB_TOOL_DIST_STORE`;
  (2) `fetch=toolchain` (JDK auto-provisioning) unless argv carries
      `-Porg.gradle.java.installations.auto-download=false`.
A refused fetch fails the run (exit 1), as the real wrapper would without a network.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path

AUTO_DOWNLOAD_OFF = "-Porg.gradle.java.installations.auto-download=false"
WRAPPER_MAIN = "org.gradle.wrapper.GradleWrapperMain"
FETCH_LOGGER = Path(__file__).resolve().parent / "fetch_logger.py"


def stdin_state() -> tuple[str, int]:
    """(state, bytes read): `tty`, `closed`, `eof`, `idle` (open, nothing to read) or `data`."""
    try:
        if os.isatty(0):
            return "tty", 0
        readable, _, _ = select.select([0], [], [], 0)
    except (OSError, ValueError):
        return "closed", 0
    if not readable:
        return "idle", 0
    data = os.read(0, 1 << 16)
    return ("data", len(data)) if data else ("eof", 0)


def _isatty(fd: int) -> bool:
    try:
        return os.isatty(fd)
    except OSError:
        return False


def _declared_distribution(argv: list[str]) -> str | None:
    if "-classpath" in argv:
        jar = Path(argv[argv.index("-classpath") + 1])
        props = jar.parent / "gradle-wrapper.properties"
        if props.is_file():
            for line in props.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "distributionUrl":
                    return value.strip().replace("\\:", ":").rsplit("/", 1)[-1].removesuffix(".zip")
    return os.environ.get("STUB_TOOL_DISTRIBUTION")


def _fetch(kind: str, target: str) -> int:
    proc = subprocess.run(
        [sys.executable, str(FETCH_LOGGER), "--fetch", kind, "--target", target],
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode


def wrapper_fetch(argv: list[str]) -> int:
    failed = False
    declared = _declared_distribution(argv)
    if declared is None:
        print("stub_tool: no wrapper-declared distribution", file=sys.stderr)
        return 2
    store = os.environ.get("STUB_TOOL_DIST_STORE", "")
    if not store or not (Path(store) / declared).exists():
        failed |= _fetch("distribution", declared) != 0
    if AUTO_DOWNLOAD_OFF not in argv:
        failed |= _fetch("toolchain", "jdk") != 0
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    wrapper = argv[:2] == ["--mode", "wrapper-fetch"]
    state, nbytes = stdin_state()
    path = os.environ.get("STUB_TOOL_LOG")
    if path:
        record = {
            "argv": argv,
            "env": dict(os.environ),
            "cwd": os.getcwd(),
            "isatty": {"stdin": _isatty(0), "stdout": _isatty(1), "stderr": _isatty(2)},
            "stdin": state,
            "stdin_bytes": nbytes,
            "pid": os.getpid(),
            "mode": "wrapper-fetch" if wrapper else "default",
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    if wrapper:
        return wrapper_fetch(argv[2:])
    return int(os.environ.get("STUB_TOOL_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
