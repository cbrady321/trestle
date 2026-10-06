"""One served kernel on a home, for the slot-pool tests (v0.4 step 1b), driven on stdin.

    python pool_server.py <home> <plugin dir> <overrides JSON>

It builds a kernel as `trestle serve` does (start pass, server lock, reaper thread, the 250 ms
pass) and answers one JSON line per command line: `{"op": "run", "plugin": ..., "args": {...}}`
(the run view, or the refusal; `after` and `idempotency_key` are optional) and `{"op": "drain"}`
(what SIGTERM does, which it also handles).
Overrides shorten the pool's periods (`{"pool": {"RESERVE_FRESH_S": 2.0}}`), the reaper's
(`"reap_interval_s"`), or stop a granted run from ever being spawned (`"hold_dispatch"`: the
grant is reported as `{"event": "granted", "run_id": ...}` and nothing starts).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from trestle.common.types import RequestOutcome, WorkOrder
from trestle.server import pool as pools
from trestle.server.main import create_kernel

NO_WAIT_MS = 0

_out = threading.Lock()


def emit(payload: dict[str, Any]) -> None:
    with _out:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


def main() -> int:
    home, plugin_dir, overrides = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
    for name, value in overrides.get("pool", {}).items():
        setattr(pools, name, value)
    kernel = create_kernel(home=home, plugin_dirs=[plugin_dir])
    scheduler = kernel.control.scheduler
    if kernel.reaper is not None and "reap_interval_s" in overrides:
        kernel.reaper.interval_s = float(overrides["reap_interval_s"])
    if overrides.get("hold_dispatch"):

        def hold(order: WorkOrder) -> None:
            emit({"event": "granted", "run_id": order.run_id})

        scheduler.on_dispatch = hold
    kernel.start_service()

    def drain(_signum: int, _frame: object | None) -> None:
        scheduler.draining = True

    signal.signal(signal.SIGTERM, drain)
    emit({"event": "ready", "server_id": kernel.server_id, "pid": os.getpid()})
    for line in sys.stdin:
        command = json.loads(line)
        if command["op"] == "drain":
            scheduler.draining = True
            emit({"answer": {"draining": True}})
            continue
        view = kernel.control.run(
            command["plugin"],
            command.get("args", {}),
            wait_ms=NO_WAIT_MS,
            after=command.get("after"),
            idempotency_key=command.get("idempotency_key"),
        )
        if isinstance(view, RequestOutcome):
            emit({"answer": view.to_dict()})
        else:
            emit({"answer": {"run_id": view.run_id, "state": view.state}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
