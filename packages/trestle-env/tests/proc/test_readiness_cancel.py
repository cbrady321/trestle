"""Cancel during a readiness wait on a local HTTP app that never answers ready (L.RB-2.3;
MC-21, WR-DEADLINE-4). PROC, venue BOTH: no Docker.

The tree's supporting service runs as its agent-launched realization, the stdlib app
`tests/fixtures/apps/http_app.py` in `never` mode (it listens, and `/health` is always 503),
launched by the real `LocalProcessPort` and read by the composition root's real HTTP read facet
over the declared contract. The cancel is raised while the loop waits its declared readiness
interval. Every claim is read from the run's lane and the app's own event log:

* the run ends cancelled: the supporting node's end is cut `stopped`, the dependent never started;
* within MC-09's stop bound: from the flag going up to the loop's return, measured on the wall
  clock (the app is a real process the release walk stops);
* no check after the stop: the stop-row offset is where the lane and the evidence stream stood
  when the flag went up (the rig's lane has no host-written stop row; the flag is the stop). Past
  it there is no observation of the supporting node, no `/health` request reaching the app (its
  own log) and no effect but the release of what the run created (stop, released).
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.proof import tolerances
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.declarations import (
    RealizationKind,
)
from trestle.workflow.values import StopCause
from trestle_packs.process.local import LocalProcessPort
from twin.twin_engine import TEST_NODE, Probe, entry_with_test

from trestle_env import schema, tree
from trestle_env.plugins._http import HttpReadinessReads

APP = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "apps" / "http_app.py"
CANCEL_AT = 3  # the flag goes up during the loop's third readiness wait


class Flagged:
    """Wraps the rig's cancel signal. When the flag first goes up it notes the wall-clock moment,
    the stop-row offset (how many lane rows and evidence events the run had written by then) and
    how many `/health` requests the app had answered."""

    def __init__(self, rig: tk.TreeRig, log: Path) -> None:
        self._rig = rig
        self._inner = rig.rig.cancel
        self._log = log
        self.raised_at: float | None = None
        self.rows_before = 0
        self.events_before = 0
        self.health_before = 0

    def wait(self, timeout: timedelta) -> bool:
        stopped = self._inner.wait(timeout)
        if stopped and self.raised_at is None:
            self.raised_at = time.monotonic()
            self.rows_before = len(self._rig.rows())
            self.events_before = len(self._rig.rig.sink.events)
            self.health_before = health(self._log)
        return bool(stopped)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def health(log: Path) -> int:
    return sum(1 for e in log.read_text().splitlines() if e.startswith("health"))


@pytest.mark.proves(
    "WR-DEADLINE-4", "WR-DEADLINE-4:b-readiness-cancel-prompt", "B", "B", "PROC", "BOTH"
)
def test_cancel_during_readiness_wait_prompt(tmp_path: Path) -> None:
    log = tmp_path / "app-events.log"
    resolved = ports.Resolved(sys.executable, "3.12", "pin", "adoption")
    command = ports.BoundCommand(
        "app",
        (sys.executable, str(APP), "never"),
        {"PORT": "0", "APP_EVENT_LOG": str(log), "PATH": "/usr/bin:/bin"},
        resolved,
        False,
    )
    spec = ports.ResourceSpec(
        tree.HTTP_SUPPORT_SERVICE, RealizationKind.AGENT_LAUNCHED_PROJECT, "http-app", command
    )
    local = LocalProcessPort()
    dependent = Probe(TEST_NODE)  # the catalog test's node: it must never be observed
    entry = entry_with_test(overrides=False)  # backend.http_support is this rig's own unit
    rig = tk.tree_rig(
        tmp_path,
        entry.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        {
            tree.POSTGRES_UNIT: Probe(tree.POSTGRES_UNIT),
            TEST_NODE: dependent,
            tree.HTTP_SUPPORT_UNIT: tree.ServiceUnit(
                tree.HTTP_SUPPORT_UNIT,
                tree.HTTP_SUPPORT_SERVICE,
                tree.HTTP_SUPPORT_READY,
                spec=spec,
            ),
        },
        port_impl={
            ports.ResourceReads: HttpReadinessReads(local, tree.HTTP_READINESS, alive="ready"),
            ports.ResourceCreate: local,
            ports.ResourceOwned: local,
        },
        deadline_s=tree.DEADLINE_S,
        request={schema.ENV_ARG: "proc"},
    )
    cancel = rig.rig.cancel
    cancel.stop_on_wait = CANCEL_AT
    cancel.stop_cause = StopCause.CANCEL
    flagged = Flagged(rig, log)
    rig.rig.services._cancel = flagged  # noqa: SLF001  (the signal the loop reads)
    rig.run()
    returned = time.monotonic()

    # the flag went up during a readiness wait, and the loop returned within the stop bound
    assert flagged.raised_at is not None, "the cancel was raised during a readiness wait"
    assert len(cancel.waits) == CANCEL_AT
    assert returned - flagged.raised_at <= tolerances.stop_bound()

    rows = rig.rows()
    ends = rig.ends()
    support = ends[tree.HTTP_SUPPORT_SERVICE]
    assert (support["condition"], support["cut"]) == ("converging", "stopped")
    assert dependent.observed == 0, "the dependent never started"
    assert ends[TEST_NODE]["cut"] == "not_started"
    assert ends[""]["cut"] == "stopped"  # the root: cancelled, not failed or timed out

    # past the stop-row offset: no check of the supporting node (no observation event, no
    # `/health` request reaching the app) and no effect but its release
    checks_after = [
        kind
        for kind, fields in rig.rig.sink.events[flagged.events_before :]
        if kind == "step.observed"
    ]
    assert checks_after == [], checks_after
    assert "step.cleanup" in [kind for kind, _ in rig.rig.sink.events[flagged.events_before :]]
    effects_after = [
        (row["class"], row.get("effect"))
        for row in rows[flagged.rows_before :]
        if row["class"] in ("issue", "confirmation", "released")
    ]
    assert effects_after == [("issue", "stop"), ("confirmation", "stop"), ("released", "up")]
    assert flagged.health_before >= 1, "the app was asked before the cancel"
    assert health(log) == flagged.health_before, "no /health request after the flag"
    assert log.read_text().splitlines()[-1] == "stop"  # the app was stopped with the run
