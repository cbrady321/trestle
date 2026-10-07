"""The system test starts only after every readiness pass, on a real local HTTP app (L.RB-5.2; B4.1,
WR-VERIFY-2). PROC, venue BOTH: no Docker.

The tree is the reference tree over a catalog that lists one test: its node (the REAL `TaskUnit`,
bound to the execution port) `needs` both backends. The supporting service runs as its
agent-launched realization, the stdlib app `tests/fixtures/apps/http_app.py` in `ready-after 3`
mode, launched by the real `LocalProcessPort` and read by the real HTTP read facet over the
declared contract; the other backend is a ready stand-in. Every claim is read from the run's lane
and the app's own event log, never from timing: the test node's first entry, its ticket, follows
the supporting node's readiness pass (the declared response on the fourth request) and the other
backend's."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind
from trestle_packs.process.local import LocalProcessPort
from twin.local_app import APP, REFUSED
from twin.twin_engine import TEST_NODE, Probe, entry_with_test

from trestle_env import schema, tree
from trestle_env.plugins._http import HttpReadinessReads
from trestle_env.plugins._tasks import TaskExecution


@pytest.mark.proves("WR-VERIFY-2", "B4.1", "B", "B", "PROC", "BOTH")
def test_system_test_starts_after_local_http_readiness_pass(tmp_path: Path) -> None:
    log = tmp_path / "app-events.log"
    resolved = ports.Resolved(sys.executable, "3.12", "pin", "adoption")
    command = ports.BoundCommand(
        "app",
        (sys.executable, str(APP), "ready-after", str(REFUSED)),
        {"PORT": "0", "APP_EVENT_LOG": str(log), "PATH": "/usr/bin:/bin"},
        resolved,
        False,
    )
    spec = ports.ResourceSpec(
        tree.HTTP_SUPPORT_SERVICE, RealizationKind.AGENT_LAUNCHED_PROJECT, "http-app", command
    )
    local = LocalProcessPort()
    entry = entry_with_test(overrides=False)  # backend.http_support is this rig's own unit
    rig = tk.tree_rig(
        tmp_path,
        entry.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        {
            tree.HTTP_SUPPORT_UNIT: tree.ServiceUnit(
                tree.HTTP_SUPPORT_UNIT,
                tree.HTTP_SUPPORT_SERVICE,
                tree.HTTP_SUPPORT_READY,
                spec=spec,
            ),
            tree.POSTGRES_UNIT: Probe(tree.POSTGRES_UNIT),
            TEST_NODE: entry.units[TEST_NODE],  # the production node: it runs nothing here
        },
        port_impl={
            ports.ResourceReads: HttpReadinessReads(local, tree.HTTP_READINESS, alive="ready"),
            ports.ResourceCreate: local,
            ports.ResourceOwned: local,
            ports.ExecutionPort: TaskExecution(None, None),
        },
        deadline_s=tree.DEADLINE_S,
        request={schema.ENV_ARG: "proc"},
    )
    rig.run()
    rows = rig.rows()
    ends = rig.ends()
    for node in (tree.HTTP_SUPPORT_SERVICE, tree.POSTGRES_SERVICE, TEST_NODE):
        assert ends[node]["condition"] == "satisfied", (node, ends[node])

    def first(path: str) -> int:
        return next(n for n, row in enumerate(rows) if row.get("path") == path)

    def end(path: str) -> int:
        return next(
            n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
        )

    # every readiness pass precedes the test node's start entry (its ticket)
    assert rows[first(TEST_NODE)]["class"] == "issue"
    assert end(tree.HTTP_SUPPORT_SERVICE) < first(TEST_NODE)
    assert end(tree.POSTGRES_SERVICE) < first(TEST_NODE)
    # the readiness pass was the declared response after REFUSED refusals: the app said so itself
    health = [e for e in log.read_text().splitlines() if e.startswith("health")]
    assert health == ["health 503"] * REFUSED + ["health 200"]
