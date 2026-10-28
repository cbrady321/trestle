"""Lane C fixtures: every process a lane-C test starts is reaped.

The conductor spawns each run's wrapper as a child of the pytest process
(`start_new_session`), so a test that abandons a run mid-flight would leave
a detached process behind. The autouse fixture below snapshots the process
table before and after each test and reaps (`ancestry.reap`) every new
ppid-descendant of this process. Only descendants are touched: other lanes
run concurrently on this host and their processes are never candidates.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests.proof import ancestry


def _descendants(root_pid: int, procs: set[ancestry.ProcInfo]) -> set[ancestry.ProcInfo]:
    found: dict[int, ancestry.ProcInfo] = {}
    changed = True
    while changed:
        changed = False
        for proc in procs:
            if proc.pid in found:
                continue
            if proc.ppid == root_pid or proc.ppid in found:
                found[proc.pid] = proc
                changed = True
    return set(found.values())


@pytest.fixture(autouse=True)
def reap_descendants() -> Iterator[None]:
    before = {(p.pid, p.start) for p in ancestry.snapshot()}
    yield
    after = ancestry.snapshot()
    leftovers = {p for p in _descendants(os.getpid(), after) if (p.pid, p.start) not in before}
    ancestry.reap(leftovers)
