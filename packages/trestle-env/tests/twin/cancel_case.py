"""One root cancel during readiness across all three resource kinds (L.RB-10.1; B6.1,
WR-CANCEL-4, WR-OWN-2), shared by the HOST node `host/test_cancel_readiness.py` and its twin
`twin/test_cancel_readiness_twin.py`.

Both publish the test plugin `fixtures/cancel_readiness.py` (a deviation: the reference tree has
no owned process during readiness, see its docstring) in a kernel, start it with `wait_ms=0`, wait
until the created container is confirmed and the owned app has answered at least one readiness
request (so the run is mid-wait), cancel the root, and await the terminal answer. The facts:

* the answer is the cancel's (`root_stop == "cancel"`), within MC-09's stop bound of the cancel;
* the owned process is gone: it logged `stop` and its port no longer accepts a connection (the
  app runs with `PORT=0` and the test reads the port it reported, `reported_port`);
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
from trestle.workflow.values import Lineage, NodePath
from trestle_packs.process.local import ENDPOINT_DIR_PREFIX, run_scoped_selector

from trestle_env import tree

REPO = Path(__file__).resolve().parents[4]
PLUGIN = Path(__file__).resolve().parents[1] / "fixtures" / "cancel_readiness.py"
HTTP_APP = REPO / "tests" / "fixtures" / "apps" / "http_app.py"
PLUGIN_NAME = "cancel_readiness"
APP_UNIT = "backend.app"


def app_environ(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """The plugin's environment: the app takes `PORT=0` (it chooses and reports its own port)."""
    log = tmp_path / "app-events.log"
    return (
        {
            "TRESTLE_K10_APP_PORT": "0",
            "TRESTLE_K10_APP_LOG": str(log),
            "TRESTLE_K10_HTTP_APP": str(HTTP_APP),
        },
        log,
    )


def reported_port(kernel_: Kernel, run_id: str) -> int | None:
    """The port the run's owned app reported (`127.0.0.1:<port>`), or None if there is no such file
    (yet, or any more). The plugin's local process port keeps one endpoint file per owned process,
    named by its run-scoped selector, in a directory of its own under the run's temp dir
    (`work/tmp`, the child's `TMPDIR`)."""
    where = run_dir(kernel_, run_id)
    if where is None:
        return None
    selector = run_scoped_selector(Lineage(run_id, NodePath((APP_UNIT,))), tree.UP)
    found = list((where / "work" / "tmp").glob(f"{ENDPOINT_DIR_PREFIX}*/{selector}.endpoint"))
    if len(found) != 1:
        return None
    return int(found[0].read_text().strip().rsplit(":", 1)[1])


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
    """Until the created container is confirmed and the app has been asked once for readiness
    (the record's wait, `records.await_confirmations`, with the app's own log read alongside)."""
    bound = tolerances.JOIN_WAIT_S * 6
    deadline = time.monotonic() + bound
    while (where := run_dir(kernel_, run_id)) is None and time.monotonic() < deadline:
        time.sleep(tolerances.POLL_S)  # admission names the run directory first
    assert where is not None, "the run never got a run directory"
    created = records.await_record(
        where,
        lambda lane, _: (
            health(log) >= 1
            and any(
                r.cls == "confirmation"
                and r.path == tree.HTTP_SUPPORT_UNIT
                and r.entry["effect"] == tree.UP
                and r.entry["status"] == "applied"
                for r in lane.rows
            )
        ),
        bound_s=max(deadline - time.monotonic(), 0.0),
    )
    assert created.why == "condition", (
        f"the run never reached its readiness wait with the container created ({created.why})"
    )


def cancel_mid_readiness(kernel_: Kernel, env: str, log: Path) -> tuple[RunView, float, int]:
    """Start, wait until mid-readiness, cancel; the terminal view, the cancel-to-end time and the
    port the app reported."""
    started = kernel_.control.run(plugin=PLUGIN_NAME, args={"env": env}, wait_ms=0)
    assert isinstance(started, RunView), started
    mid_wait(kernel_, started.run_id, log)
    port = reported_port(kernel_, started.run_id)
    assert port is not None, "the app is answering readiness, so it has reported its port"
    asked = time.monotonic()
    kernel_.control.cancel(started.run_id)
    done = kernel_.control.project.await_terminal(started.run_id)
    elapsed = time.monotonic() - asked
    assert isinstance(done, RunView), done
    return done, elapsed, port


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
