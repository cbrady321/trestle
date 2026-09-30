"""The readiness ordering on the fake binding: the CI twin of the host node (L.RB-2.1; MC-B-03).

The tree's own units run through the loop on `FakeContainerEngine`, whose supporting container
serves the declared HTTP response only after `REFUSED` refused requests (the fake's stand-in for
the read facet that makes the real request). The facts are the host node's: read from the run's
lane, the readiness pass precedes the dependent's first entry."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.values import CheckResult
from trestle_packs.fakes.container import FakeContainerEngine

from trestle_env import schema, tree

REFUSED = 3  # the fake serves the declared response from the request after this many refusals


class SupportEngine(FakeContainerEngine):
    """A fake Docker engine whose declared checks answer as the real ones would.

    `http_support_ready` is satisfied once the container has been asked `REFUSED` times before
    (`serves_declared` False: it is up and listening but never serves the declared response);
    `postgres_ready` once the container runs (`password_ok` False: authentication fails)."""

    def __init__(self, *, serves_declared: bool = True, password_ok: bool = True) -> None:
        super().__init__()
        self.serves_declared = serves_declared
        self.password_ok = password_ok
        self.asked: list[str] = []

    def check(self, check: str, target: Any) -> CheckResult:
        running = super().check("running", target)
        if check == "running" or not running.satisfied:
            return running
        self.asked.append(check)
        if check == tree.HTTP_SUPPORT_READY:
            asked = self.asked.count(check)
            ok = self.serves_declared and asked > REFUSED
            return CheckResult(ok, None, f"GET {tree.HTTP_SUPPORT_READINESS.path}: answer {asked}")
        if check == tree.POSTGRES_READY:
            return CheckResult(self.password_ok, None, "psql SELECT 1")
        return CheckResult(False, None, f"{check} is not a check this engine knows")


def rig_over(tmp_path: Path, engine: SupportEngine) -> tk.TreeRig:
    return tk.tree_rig(
        tmp_path,
        tree.ENTRY.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        {
            tree.HTTP_SUPPORT_UNIT: tree.ENTRY.units[tree.HTTP_SUPPORT_UNIT],
            tree.POSTGRES_UNIT: tree.ENTRY.units[tree.POSTGRES_UNIT],
        },
        port_impl={
            ports.ResourceReads: engine,
            ports.ResourceCreate: engine,
            ports.ResourceOwned: engine,
            ports.ResourceSafeStart: engine,
        },
        deadline_s=tree.DEADLINE_S,
        request={schema.ENV_ARG: "twin"},
    )


def first_row(rows: list[dict[str, Any]], path: str) -> int:
    return next(n for n, row in enumerate(rows) if row.get("path") == path)


def end_row(rows: list[dict[str, Any]], path: str) -> int:
    return next(
        n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
    )


@pytest.mark.stub_proven("WR-VERIFY-2:b-host-ordering@stub-twin")
@pytest.mark.stub_proven("WR-ENV-10:readiness-authoritative-http@stub-twin")
def test_dependent_starts_after_http_readiness_pass(tmp_path: Path) -> None:
    engine = SupportEngine()
    rig = rig_over(tmp_path, engine)
    rig.run()
    rows = rig.rows()
    ends = rig.ends()
    assert ends[tree.HTTP_SUPPORT_UNIT]["condition"] == "satisfied"
    assert ends[tree.POSTGRES_UNIT]["condition"] == "satisfied"
    # the readiness pass precedes the dependent's first entry (its create is issued after it)
    assert end_row(rows, tree.HTTP_SUPPORT_UNIT) < first_row(rows, tree.POSTGRES_UNIT)
    # the pass is the answer to the declared check, polled REFUSED + 1 times on the declared wait
    assert engine.asked.count(tree.HTTP_SUPPORT_READY) == REFUSED + 1
    assert engine.asked.index(tree.POSTGRES_READY) > max(
        n for n, name in enumerate(engine.asked) if name == tree.HTTP_SUPPORT_READY
    )
    polls = [w for w in rig.rig.cancel.waits if w == timedelta(seconds=tree.READY_POLL_S)]
    assert len(polls) >= REFUSED + 1
    # both containers were released with the run: nothing of the run is left on the engine
    assert engine.inventory()["containers"] == frozenset()


def test_a_running_support_container_without_the_declared_response_starts_no_dependent(
    tmp_path: Path,
) -> None:
    """Up is not ready: the container runs (and would accept a connection) but never serves the
    declared response, so its readiness never passes and the backend is never created."""
    engine = SupportEngine(serves_declared=False)
    rig = rig_over(tmp_path, engine)
    rig.run()
    rows = rig.rows()
    ends = rig.ends()
    assert ends[tree.HTTP_SUPPORT_UNIT]["condition"] != "satisfied"
    assert not any(
        row.get("path") == tree.POSTGRES_UNIT and row["class"] == "issue" for row in rows
    )
    assert tree.POSTGRES_READY not in engine.asked
    assert engine.inventory()["containers"] == frozenset()  # what it created is released
