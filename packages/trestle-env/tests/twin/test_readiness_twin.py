"""The readiness ordering on the fake binding: the CI twin of the host node (L.RB-2.1; MC-B-03).

The tree's own units run through the loop on `FakeContainerEngine`, whose supporting container
serves the declared HTTP response only after `REFUSED` refused requests (the fake's stand-in for
the read facet that makes the real request). The facts are the host node's: read from the run's
lane, the readiness pass precedes the dependent's (the test node's) first entry."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from trestle_env import tree
from twin.twin_engine import (
    REFUSED,
    TEST_NODE,
    SupportEngine,
    end_row,
    entry_with_test,
    first_row,
    rig_over,
)


@pytest.mark.stub_proven("WR-VERIFY-2:b-host-ordering@stub-twin")
@pytest.mark.stub_proven("WR-ENV-10:readiness-authoritative-http@stub-twin")
def test_dependent_starts_after_http_readiness_pass(tmp_path: Path) -> None:
    engine = SupportEngine()
    rig = rig_over(tmp_path, engine, entry_with_test())
    rig.run()
    rows = rig.rows()
    ends = rig.ends()
    assert ends[tree.HTTP_SUPPORT_SERVICE]["condition"] == "satisfied"
    assert ends[tree.POSTGRES_SERVICE]["condition"] == "satisfied"
    assert ends[TEST_NODE]["condition"] == "satisfied"
    # the readiness pass precedes the dependent's first entry (its ticket is issued after it)
    assert end_row(rows, tree.HTTP_SUPPORT_SERVICE) < first_row(rows, TEST_NODE)
    assert end_row(rows, tree.POSTGRES_SERVICE) < first_row(rows, TEST_NODE)
    # the pass is the answer to the declared check, polled REFUSED + 1 times on the declared wait
    assert engine.asked.count(tree.HTTP_SUPPORT_READY) == REFUSED + 1
    polls = [w for w in rig.rig.cancel.waits if w == timedelta(seconds=tree.READY_POLL_S)]
    assert len(polls) >= REFUSED + 1
    # both containers were released with the run: nothing of the run is left on the engine
    assert engine.inventory()["containers"] == frozenset()


def test_a_running_support_container_without_the_declared_response_starts_no_dependent(
    tmp_path: Path,
) -> None:
    """Up is not ready: the container runs (and would accept a connection) but never serves the
    declared response, so its readiness never passes and the dependent is never started."""
    engine = SupportEngine(serves_declared=False)
    rig = rig_over(tmp_path, engine, entry_with_test())
    rig.run()
    rows = rig.rows()
    ends = rig.ends()
    assert ends[tree.HTTP_SUPPORT_SERVICE]["condition"] != "satisfied"
    assert ends[TEST_NODE]["cut"] == "not_started"
    assert not any(row.get("path") == TEST_NODE and row["class"] == "issue" for row in rows)
    assert engine.inventory()["containers"] == frozenset()  # what it created is released
