"""G-A3 (BFD-05), flipped by L.CS-2.4: recovery stops the run's recorded group.

A server SIGKILLed mid-run leaves the wrapper, child and grandchild running. `recover_run_dir`
reads the run's `process_identity` rows, finds the recorded leader alive with a matching start
(B2-C11 branch (iii)) and stops every attributable process before it finalizes `interrupted`.
"""

from __future__ import annotations

import shutil

import pytest

from tests.pins.a_lifecycle import helpers
from tests.proof import mcp_host, tolerances
from tests.proof.markers import target_check


def _kill_and_restart(host: mcp_host.McpHost) -> tuple[helpers.Tree, str | None, set]:
    """Start a `detach` run over MCP, SIGKILL the server mid-run, restart on
    the same home, and read the run back. Returns (live tree, terminal,
    survivors after recovery)."""
    plugins = host.home / "plugins"
    shutil.copy(helpers.LANE_PLUGIN_DIR / "detach.py", plugins / "detach.py")
    started = host.call("run", {"plugin": "detach", "wait_ms": 0})
    run_id = started["run_id"]
    with helpers.reaping(run_id):
        run_dir = helpers.find_run_dir(host.home, run_id)
        helpers.wait_ready(run_dir)
        tree = helpers.Tree(marker=run_id, live=helpers.marked(run_id))
        assert tree.wrapper and tree.child and tree.grandchild, tree.live

        host.kill_server()
        host.restart()

        terminal = helpers.terminal_of(run_dir)
        helpers.wait_until(
            lambda: not helpers.alive_marked(tree.live, run_id), tolerances.PROC_WAIT_S
        )
        return tree, terminal, helpers.alive_marked(tree.live, run_id)


@pytest.mark.proves("A6.2", "A6.2:core", "A", "core", "PROC", "BOTH")
@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:recovery-stops-recorded-group", "core", "core", "PROC", "BOTH"
)
def test_target_restart_leaves_no_attributable_survivor() -> None:
    with mcp_host.McpHost() as host:
        _tree, _terminal, survivors = _kill_and_restart(host)
    target_check(
        not survivors,
        "G-A3",
        f"{len(survivors)} attributable process(es) alive after recovery finished",
    )
