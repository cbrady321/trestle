"""G-A3 (BFD-05): server SIGKILL leaves wrapper, child and grandchild;
recovery only marks the run interrupted.

`recover_run_dir` reads ledger kinds, does no liveness check and signals
nothing, so every process of the killed run's tree outlives recovery.
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


@pytest.mark.pin("G-A3")
def test_pin_orphans_survive_restart_run_interrupted() -> None:
    with mcp_host.McpHost() as host:
        tree, terminal, survivors = _kill_and_restart(host)
    assert terminal == "interrupted"
    assert tree.wrapper <= survivors, "wrapper did not survive server SIGKILL + recovery"
    assert tree.child <= survivors, "child did not survive server SIGKILL + recovery"
    assert tree.grandchild <= survivors, "grandchild did not survive server SIGKILL + recovery"


@pytest.mark.target("G-A3")
@pytest.mark.proves("A6.2", "A6.2:core", "A", "core", "PROC", "BOTH")
@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:recovery-stops-recorded-group", "core", "core", "PROC", "BOTH"
)
@pytest.mark.xfail(strict=True, reason="defect:G-A3")
def test_target_restart_leaves_no_attributable_survivor() -> None:
    with mcp_host.McpHost() as host:
        _tree, _terminal, survivors = _kill_and_restart(host)
    target_check(
        not survivors,
        "G-A3",
        f"{len(survivors)} attributable process(es) alive after recovery finished",
    )
