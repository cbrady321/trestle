"""One root cancel during readiness across all three resource kinds (L.RB-10.1; B6.1,
WR-CANCEL-4, WR-OWN-2), shared by the HOST node `host/test_cancel_readiness.py` and its twin
`twin/test_cancel_readiness_twin.py`.

Both publish the test plugin `fixtures/cancel_readiness.py` (a deviation: the reference tree has
no owned process during readiness, see its docstring) in a kernel, start it with `wait_ms=0`, wait
until the created container is confirmed and the owned app has answered at least one readiness
request (so the run is mid-wait), cancel the root, and await the terminal answer. The facts:

* the answer is the cancel's (`root_stop == "cancel"`), within MC-09's stop bound of the cancel;
* the owned process is gone: it logged `stop` and its port no longer accepts a connection;
* past the stop row's offset (the ledger's `stop_row`, its `lane_committed_length`) the lane
  holds no effect but releases (`issue`/`confirmation` of the declared release effect,
  `released`); the found container is never the subject of any row;
* the created container and the found one are checked by the caller on its own engine.
"""

from __future__ import annotations

import shutil
import socket
import time
from pathlib import Path
from typing import Any

from tests.proof import records, tolerances
from trestle.common.types import RunView
from trestle.server.main import Kernel, create_kernel

from trestle_env import tree

REPO = Path(__file__).resolve().parents[4]
PLUGIN = Path(__file__).resolve().parents[1] / "fixtures" / "cancel_readiness.py"
HTTP_APP = REPO / "tests" / "fixtures" / "apps" / "http_app.py"
PLUGIN_NAME = "cancel_readiness"
APP_UNIT = "backend.app"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def app_environ(tmp_path: Path) -> tuple[dict[str, str], Path, int]:
    port, log = free_port(), tmp_path / "app-events.log"
    return (
        {
            "TRESTLE_K10_APP_PORT": str(port),
            "TRESTLE_K10_APP_LOG": str(log),
            "TRESTLE_K10_HTTP_APP": str(HTTP_APP),
        },
        log,
        port,
    )


def kernel(tmp_path: Path) -> Kernel:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    shutil.copy(PLUGIN, plugins / PLUGIN.name)
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[plugins], skip_recovery=True)
    built.registry.refresh()
    return built


def lane(run_dir: Path) -> list[dict[str, Any]]:
    rows = records.lane_rows(run_dir)
    assert not rows.problems and not rows.torn, rows.problems
    return [row.entry for row in rows.rows]


def run_dir(kernel_: Kernel, run_id: str) -> Path | None:
    found = sorted((kernel_.home / "runs").glob(f"*/{run_id}"))
    return found[0] if found else None


def health(log: Path) -> int:
    if not log.exists():
        return 0
    return sum(1 for e in log.read_text().splitlines() if e.startswith("health"))


def mid_wait(kernel_: Kernel, run_id: str, log: Path) -> None:
    """Until the created container is confirmed and the app has been asked once for readiness."""
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S * 6
    while time.monotonic() < deadline:
        where = run_dir(kernel_, run_id)
        written = where is not None and (where / "evidence" / "lane.ndjson").exists()
        rows = lane(where) if written and where is not None else []
        created = [
            e
            for e in rows
            if e["class"] == "confirmation"
            and e.get("path") == tree.HTTP_SUPPORT_UNIT
            and e.get("effect") == tree.UP
            and e.get("status") == "applied"
        ]
        if created and health(log) >= 1:
            return
        time.sleep(tolerances.POLL_S)
    raise AssertionError("the run never reached its readiness wait with the container created")


def cancel_mid_readiness(kernel_: Kernel, env: str, log: Path) -> tuple[RunView, float]:
    """Start, wait until mid-readiness, cancel; the terminal view and the cancel-to-end time."""
    started = kernel_.control.run(plugin=PLUGIN_NAME, args={"env": env}, wait_ms=0)
    assert isinstance(started, RunView), started
    mid_wait(kernel_, started.run_id, log)
    asked = time.monotonic()
    kernel_.control.cancel(started.run_id)
    done = kernel_.control.project.await_terminal(started.run_id)
    elapsed = time.monotonic() - asked
    assert isinstance(done, RunView), done
    return done, elapsed


def assert_cancel_facts(
    kernel_: Kernel, done: RunView, elapsed: float, log: Path, port: int, found: str
) -> list[dict[str, Any]]:
    answer = done.answer
    assert answer is not None, done
    assert answer["root_stop"] == "cancel", answer
    assert answer["outcome"] == "cancelled", answer
    assert elapsed <= tolerances.stop_bound(), elapsed
    # the owned process: told to stop, and no longer listening
    assert log.read_text().splitlines()[-1] == "stop"
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "the owned app still listens"
    where = run_dir(kernel_, done.run_id)
    assert where is not None
    entries = lane(where)
    # the stop row is the ledger's (B2-C15): the lane length committed when the cancel landed
    stops = [r for r in records.ledger_rows(where).rows if r.get("kind") == "stop_row"]
    assert stops and stops[0]["cause"] == "cancel", stops
    offset = stops[0]["lane_committed_length"]  # in bytes of the lane file
    raw = (where / "evidence" / "lane.ndjson").read_bytes()
    assert isinstance(offset, int) and 0 < offset <= len(raw), (offset, len(raw))
    after = entries[raw[:offset].count(b"\n") :]
    effects = [
        (e["class"], e.get("effect")) for e in after if e["class"] in ("issue", "confirmation")
    ]
    assert all(effect == tree.STOP for _, effect in effects), effects  # releases only
    released = {e["path"] for e in after if e["class"] == "released"}
    assert {tree.HTTP_SUPPORT_UNIT, APP_UNIT} <= released, released
    assert all(str(e.get("identity", "")) != found for e in entries)  # the found one: no row
    return entries
