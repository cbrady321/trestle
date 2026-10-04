"""One healthy-machine call: a created container, a reused found one and an owned app killed and
repaired, in one run (L.RB-8.3; WR-ENV-10, B7.1), shared by the HOST node
`host/test_healthy_machine_full.py` and its twin `twin/test_healthy_machine_full_twin.py`.

Both publish the test plugin `fixtures/owned_restart.py` (a deviation: the reference service unit
declares no owned-restart remedy, see its docstring) in a kernel, start it with `wait_ms=0`, wait
until the created container is confirmed and the owned app has answered a readiness request (so
the run is inside the app's wait), SIGKILL the app, and await the terminal answer. The facts, read
from the answer's per-node accounts, the lane and the app's own event log:

* the run is `passed`; `backend.http_support` STARTED, `backend.postgres` REUSED, `backend.app`
  REPAIRED (the disposition is the app's own; the class is never a fourth outcome);
* exactly one `restart` of the app, and one create of each created resource;
* the app is a new process (it listened twice, no `stop` between them) and was stopped with the run;
* the found container is never the subject of an issue or a confirmation.

The created container, the found one and its volume are checked by the caller on its own engine.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
from pathlib import Path
from typing import Any

from trestle.common.types import RunView
from trestle.server import answer as answer_mod
from trestle.server.main import Kernel, create_kernel

from trestle_env import tree
from twin import cancel_case

PLUGIN = Path(__file__).resolve().parents[1] / "fixtures" / "owned_restart.py"
PLUGIN_NAME = "healthy_machine"
APP_UNIT = "backend.app"
HTTP_APP = cancel_case.HTTP_APP


def app_environ(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """The plugin's environment: the app takes `PORT=0` and reports its own port (see
    `cancel_case.reported_port`)."""
    log = tmp_path / "app-events.log"
    return (
        {
            "TRESTLE_B7_APP_PORT": "0",
            "TRESTLE_B7_APP_LOG": str(log),
            "TRESTLE_B7_HTTP_APP": str(HTTP_APP),
        },
        log,
    )


def kernel(tmp_path: Path) -> Kernel:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    shutil.copy(PLUGIN, plugins / PLUGIN.name)
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[plugins], skip_recovery=True)
    built.registry.refresh()
    return built


LSOF_PATHS = ("/usr/sbin/lsof", "/usr/bin/lsof")  # macOS ships it in /usr/sbin, Linux in /usr/bin


def listener(port: int) -> int:
    """The pid listening on the app's loopback port: `lsof` where the host has one, else the
    Linux kernel's own socket tables (`/proc`), so the probe never depends on a tool a CI image may
    lack."""
    lsof = next((p for p in LSOF_PATHS if os.access(p, os.X_OK)), None) or shutil.which("lsof")
    if lsof is None:
        pids = proc_listeners(port)
        assert len(pids) == 1, (port, pids)
        return pids[0]
    found = subprocess.run(  # noqa: S603 - the test's own probe
        [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    listed = found.stdout.split()
    assert len(listed) == 1, (found.stdout, found.stderr)
    return int(listed[0])


def proc_listeners(port: int) -> list[int]:
    """The pids holding a TCP socket that LISTENs on `port`, read from `/proc` (Linux): the socket
    inodes of `/proc/net/tcp{,6}` rows in state `0A` (LISTEN) on that local port, then every
    process whose open descriptors name one of them."""
    inodes: set[str] = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            rows = table.read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if int(fields[1].rsplit(":", 1)[1], 16) == port and fields[3] == "0A":
                inodes.add(f"socket:[{fields[9]}]")
    assert inodes, f"no /proc/net/tcp socket listens on {port} (and no lsof on this host)"
    pids: set[int] = set()
    for fds in Path("/proc").glob("[0-9]*/fd"):
        try:
            names = os.listdir(fds)
        except OSError:  # gone, or not ours to read
            continue
        for name in names:
            try:
                if os.readlink(fds / name) in inodes:
                    pids.add(int(fds.parent.name))
                    break
            except OSError:
                continue
    return sorted(pids)


def run_and_kill(kernel_: Kernel, env: str, log: Path) -> tuple[RunView, int, int]:
    """Start the run, kill the app once mid-readiness; the terminal view, the killed pid and the
    port the killed app had reported."""
    started = kernel_.control.run(plugin=PLUGIN_NAME, args={"env": env}, wait_ms=0)
    assert isinstance(started, RunView), started
    cancel_case.mid_wait(kernel_, started.run_id, log)
    assert cancel_case.health(log) >= 1 and "health 200" not in log.read_text().splitlines()
    port = cancel_case.reported_port(kernel_, started.run_id)
    assert port is not None, "the app is answering readiness, so it has reported its port"
    pid = listener(port)
    os.kill(pid, signal.SIGKILL)  # the owned app dies inside its readiness wait
    done = kernel_.control.project.await_terminal(started.run_id)
    assert isinstance(done, RunView), done
    return done, pid, port


def assert_repaired(
    kernel_: Kernel, done: RunView, pid: int, log: Path, port: int, found: str
) -> list[dict[str, Any]]:
    answer = done.answer
    assert answer is not None, done
    assert answer["outcome"] == "passed" and answer["root_stop"] is None, answer
    assert answer["error"] is None, answer
    # the disposition of each node: repaired beside passed, never a clean pass for the app
    assert answer["primary"]["path"] == [APP_UNIT], answer["primary"]
    assert answer["primary"]["node_class"] == "repaired"
    assert answer["primary"]["disposition"] == "repaired"
    where = cancel_case.run_dir(kernel_, done.run_id)
    assert where is not None
    views = answer_mod.read_child_views(where)
    assert views is not None, "the child views were written at finalization"
    shown = {key: view["disposition"] for key, view in views.items() if view is not None}
    assert shown[tree.HTTP_SUPPORT_UNIT] == "started", shown
    assert shown[tree.POSTGRES_UNIT] == "reused", shown
    assert shown[APP_UNIT] == "repaired", shown
    # the lane: one restart, one create per created resource, nothing for the found container
    entries = cancel_case.lane(where)
    issues = [(e["path"], e["effect"]) for e in entries if e["class"] == "issue"]
    assert issues.count((APP_UNIT, "restart")) == 1, issues
    assert issues.count((APP_UNIT, tree.UP)) == 1, issues
    assert issues.count((tree.HTTP_SUPPORT_UNIT, tree.UP)) == 1, issues
    assert not [i for i in issues if i[0] == tree.POSTGRES_UNIT], issues
    assert all(str(e.get("identity", "")) != found for e in entries)
    # the app: a new process after the kill (listened twice, no `stop` between), stopped at the end
    seen = log.read_text().splitlines()
    assert seen.count("listening") == 2, seen
    second = seen.index("listening", seen.index("listening") + 1)
    assert "stop" not in seen[:second] and "health 200" in seen[second:], seen
    assert seen[-1] == "stop", seen
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "the owned app still listens"
    assert pid > 0
    return entries
