"""HTTP readiness on a real local process: the dependent starts after the declared response
(L.RB-2.1; hld-wr-environment KDD 2, WR-VERIFY-2). PROC, venue BOTH: no Docker.

The tree's supporting service is run as its agent-launched realization: the stdlib app
`tests/fixtures/apps/http_app.py`, in `ready-after 3` mode, launched by the real
`LocalProcessPort`, and its readiness read by the composition root's real HTTP read facet over the
declared contract (`GET /health` answers 200 `ok`). The dependent is the tree's own `needs` edge.
Every claim is read from the run's lane and the app's own event log, never from timing."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.declarations import (
    RealizationKind,
)
from trestle_packs.process.local import LocalProcessPort
from twin.local_app import APP, REFUSED
from twin.twin_engine import TEST_NODE, Probe, entry_with_test

from trestle_env import schema, tree
from trestle_env.plugins._http import HttpReadinessReads


def rig_over_local_app(tmp_path: Path, mode: tuple[str, ...]) -> tuple[tk.TreeRig, Probe, Path]:
    log = tmp_path / "app-events.log"
    resolved = ports.Resolved(sys.executable, "3.12", "pin", "adoption")
    command = ports.BoundCommand(
        "app",
        (sys.executable, str(APP), *mode),
        {"PORT": "0", "APP_EVENT_LOG": str(log), "PATH": "/usr/bin:/bin"},
        resolved,
        False,
    )
    spec = ports.ResourceSpec(
        tree.HTTP_SUPPORT_SERVICE, RealizationKind.AGENT_LAUNCHED_PROJECT, "http-app", command
    )
    local = LocalProcessPort()
    reads = HttpReadinessReads(local, tree.HTTP_READINESS, alive="ready")
    saw: list[list[str]] = []
    dependent = Probe(TEST_NODE, lambda: saw.append(log.read_text().split("\n")))
    dependent.saw = saw  # type: ignore[attr-defined]
    entry = entry_with_test()
    units = {
        tree.HTTP_SUPPORT_UNIT: tree.ServiceUnit(
            tree.HTTP_SUPPORT_UNIT,
            tree.HTTP_SUPPORT_SERVICE,
            tree.HTTP_SUPPORT_READY,
            spec=spec,
        ),
        tree.POSTGRES_UNIT: Probe(tree.POSTGRES_UNIT),
        TEST_NODE: dependent,
    }
    rig = tk.tree_rig(
        tmp_path,
        entry.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        units,
        port_impl={
            ports.ResourceReads: reads,
            ports.ResourceCreate: local,
            ports.ResourceOwned: local,
        },
        deadline_s=tree.DEADLINE_S,
        request={schema.ENV_ARG: "proc"},
    )
    return rig, dependent, log


@pytest.mark.proves(
    "WR-VERIFY-2", "WR-VERIFY-2:b-readiness-ordering-proc", "B", "B", "PROC", "BOTH"
)
def test_dependent_starts_after_local_http_readiness_pass(tmp_path: Path) -> None:
    rig, dependent, log = rig_over_local_app(tmp_path, ("ready-after", str(REFUSED)))
    rig.run()
    rows = rig.rows()

    def first(path: str) -> int:
        return next(n for n, row in enumerate(rows) if row.get("path") == path)

    def end(path: str) -> int:
        return next(
            n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
        )

    ends = rig.ends()
    assert ends[tree.HTTP_SUPPORT_UNIT]["condition"] == "satisfied"
    assert ends[TEST_NODE]["condition"] == "satisfied"
    # the readiness pass (the supporting node's satisfied end) precedes the dependent's first entry
    assert end(tree.HTTP_SUPPORT_UNIT) < first(TEST_NODE)
    # and the app itself had answered the declared response by the time the dependent was started
    assert dependent.saw and dependent.saw[0][-3:-1] == ["health 503", "health 200"]  # then a `""`
    # the app saw exactly the polls the contract needed: REFUSED refusals, then the pass
    events = log.read_text().splitlines()
    health = [e for e in events if e.startswith("health")]
    assert events[0] == "listening"
    assert " ".join(health) == " ".join(["health 503"] * REFUSED + ["health 200"])
    assert events[-1] == "stop"  # released with the run
    # polled on the declared wait and never slept past it: one poll interval before each of the
    # REFUSED + 1 observations that follow the create, and no other wait
    waits = rig.rig.cancel.waits
    assert waits == [timedelta(seconds=tree.READY_POLL_S)] * (REFUSED + 1)
