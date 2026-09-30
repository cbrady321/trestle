#!/usr/bin/env python3
"""Stand-in for the command that starts the container engine (MC-B-06, L.RB-7.2; D-8).

Invoked only by absolute path, never on PATH and never under the name `docker`; the real docker
binary and the real engine are never involved (starting the real engine is deferred, D-8). It has
one job: make the stub engine's state file say `up`, or not.

Configuration is environment only, because the caller's argv is fixed:
  STUB_ENGINE_STATE  path of the stub engine's state file (`up` once started, `down` before)
  STUB_ENGINE_MODE   `starts` (default: the engine comes up, exit 0), `no-progress` (exit 0 and
                     the engine stays down: a start that did nothing) or `refuses` (exit 3)
  STUB_ENGINE_LOG    path; one JSON line per call `{"argv": [...], "mode": ..., "state_before": ...}`
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MODES = ("starts", "no-progress", "refuses")
REFUSED_EXIT = 3


def engine_state(path: str | None) -> str:
    """`up` or `down`: what the stub engine's state file holds (a missing file is `down`)."""
    if not path:
        return "down"
    try:
        return Path(path).read_text(encoding="utf-8").strip() or "down"
    except OSError:
        return "down"


def main(argv: list[str]) -> int:
    mode = os.environ.get("STUB_ENGINE_MODE", "starts")
    state = os.environ.get("STUB_ENGINE_STATE")
    log = os.environ.get("STUB_ENGINE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            record = {"argv": argv, "mode": mode, "state_before": engine_state(state)}
            handle.write(json.dumps(record) + "\n")
    if mode not in MODES:
        print(f"stub_engine_starter: unknown mode {mode!r}", file=sys.stderr)
        return 2
    if mode == "refuses":
        print("stub_engine_starter: the engine would not start", file=sys.stderr)
        return REFUSED_EXIT
    if mode == "starts" and state:
        Path(state).write_text("up\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
