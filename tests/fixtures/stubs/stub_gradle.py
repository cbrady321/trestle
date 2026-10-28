#!/usr/bin/env python3
"""stub_gradle: a Gradle-shaped build that shows the build-daemon patterns (MC-B-06, TM-B4-1.4,
WR-CANCEL-7, D-19: no real Gradle is ever run; JVM/Gradle is STUB-PROVEN only).

Invoked only by absolute path, as the resolved `java` of a Gradle-shaped task would be:
`<python> stub_gradle.py -classpath <gradle/wrapper/gradle-wrapper.jar>
org.gradle.wrapper.GradleWrapperMain [--no-daemon] [-P...] <task>` (B3-C14). It stands for the three
things a JVM build does with helper processes:

* WITHOUT `--no-daemon` it starts a build daemon: a process in its own session that outlives the
  build (`daemon_started` in the log). With `--no-daemon` (the declared configuration) it starts
  none, which is what "prevented by declared config" means (B3-C14, WR-CANCEL-7).
* `STUB_GRADLE_HELPER=ingroup` starts a helper process in the build's own process group (an
  in-group helper: a test worker, a compiler daemon Gradle itself manages), which the run's group
  stop ends with the run.
* `STUB_GRADLE_HELPER=escaping` starts a helper that leaves the run's process group AND session and
  is reparented away from the build (a double fork: a tool's own language server, a shell wrapper's
  detached job): outside Gradle's daemon machinery, so no group ancestry reaches it. The disclosed
  boundary (D-19, WR-CANCEL-7:disclosed-boundary).

`STUB_GRADLE_LOG` (a path) receives one JSON line per event (`start`, `daemon_started`,
`helper_started`, `done`) with the pids; `STUB_GRADLE_TAG` is put in each helper's argv so a test
finds the process table's entries of one run; `STUB_GRADLE_SECONDS` (default 0) is how long the
build itself runs before it exits 0 (a cancel test holds it long). Every helper waits for
`HELPER_SECONDS` and then exits by itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

WRAPPER_MAIN = "org.gradle.wrapper.GradleWrapperMain"
NO_DAEMON = "--no-daemon"
HELPER_SECONDS = 120.0


def log(event: str, **fields: object) -> None:
    path = os.environ.get("STUB_GRADLE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": event, "pid": os.getpid(), **fields}) + "\n")


def _spawn(role: str, *, detached: bool) -> int:
    tag = os.environ.get("STUB_GRADLE_TAG", "")
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), f"--{role}", tag],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=detached,
    )
    return child.pid


def _double_fork_helper() -> int:
    """An escaping helper: a shell-style detached job. The intermediate process exits at once, so
    the helper is reparented to init and shares nothing with the build but the log."""
    tag = os.environ.get("STUB_GRADLE_TAG", "")
    code = (
        "import os, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, sys.argv[1], '--escaping', sys.argv[2]],\n"
        "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    start_new_session=True)\n"
        "print(child.pid, flush=True)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, os.path.abspath(__file__), tag],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip())


def build(argv: list[str]) -> int:
    log("start", argv=argv, pgid=os.getpgid(0), sid=os.getsid(0))
    if NO_DAEMON not in argv:
        log("daemon_started", helper_pid=_spawn("daemon", detached=True))
    helper = os.environ.get("STUB_GRADLE_HELPER", "")
    if helper == "ingroup":
        log("helper_started", kind="ingroup", helper_pid=_spawn("helper", detached=False))
    elif helper == "escaping":
        log("helper_started", kind="escaping", helper_pid=_double_fork_helper())
    time.sleep(float(os.environ.get("STUB_GRADLE_SECONDS", "0")))
    log("done")
    return 0


def helper_main(role: str) -> int:
    time.sleep(HELPER_SECONDS)
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] in (["--daemon"], ["--helper"], ["--escaping"]):
        return helper_main(argv[0][2:])
    return build(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
