"""L.TR-6.8: the run-capacity default is the one the Slice A measurement chose (MC-B3-10; OQ-10).

CI half (`WR-TERM-7:capacity-default-measured`, PROC, CI): what is checked without measuring.

* `test_default_matches_measurement_record`: the `TrestleConfig` default of `max_running_runs`
  equals `chosen` in `capacity_slice_a.json` (recorded on darwin, Python 3.12, at the workload's
  MC-35 names), `chosen` is what the recorded samples give under the record's own `ratio_bound`, the
  samples are N = 1, 2, ... to the first failing N (or the cap), and `ratio_bound` is
  `capacity.RATIO_BOUND`; the provisional marker (TM-C3) is gone from the configuration;
* `test_documented_capacity_matches_default`: `docs/agents.md` states both capacities, which one
  was measured, and the ratio bound, equal to the configuration's and the record's values;
* `test_queue_beyond_default_fifo`: `default + 1` concurrent fake admissions leave exactly one
  queued, dispatched first in first out as slots free.

HOST half (`WR-TERM-7:capacity-default-measured@host`, PROC, HOST):
`test_default_holds_slice_a_workload` carries `host_only`, so the root plugin deselects it in every
session unless `TRESTLE_HOST_GATE=proc` (CSC-9): a CI runner is not the measured machine. On the
host it runs the Slice A workload concurrently at the default capacity and requires every run to
end within its deadline with no MC-13 survivor."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host
from tests.tree import capacity
from trestle.common.types import WorkOrder
from trestle.server import config as server_config
from trestle.server.scheduler import Scheduler

REPO = Path(__file__).resolve().parents[2]
AGENTS = REPO / "docs" / "agents.md"
LONG_DEADLINE_S = capacity.WAIT_S * 10

proves_ci = pytest.mark.proves(
    "WR-TERM-7", "WR-TERM-7:capacity-default-measured", "A", "tree", "PROC", "CI"
)
proves_host = pytest.mark.proves(
    "WR-TERM-7", "WR-TERM-7:capacity-default-measured@host", "A", "tree", "PROC", "HOST"
)


def record() -> dict[str, Any]:
    loaded = json.loads(capacity.RECORD.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def record_problems(rec: dict[str, Any], default: int) -> list[str]:
    """What is wrong with a measurement record against the configured default."""
    problems: list[str] = []
    samples = rec.get("samples") or []
    if [s["n"] for s in samples] != list(range(1, len(samples) + 1)) or not samples:
        problems.append("samples are not N = 1, 2, ... in order")
        return problems
    if rec.get("ratio_bound") != capacity.RATIO_BOUND:
        problems.append("ratio_bound is not capacity.RATIO_BOUND")
    bound = float(rec.get("ratio_bound", 0))
    if rec.get("chosen") != capacity.choose(samples, bound):
        problems.append("chosen is not the largest passing N of the samples")
    if default != rec.get("chosen"):
        problems.append(f"the configured default {default} is not chosen {rec.get('chosen')}")
    baseline = float(samples[0]["p95_s"])
    if any(not capacity.passes(s, baseline, bound) for s in samples[:-1]):
        problems.append("a level before the last failed: the measurement did not stop at it")
    if capacity.passes(samples[-1], baseline, bound) and samples[-1]["n"] < capacity.cap_default():
        problems.append("the measurement stopped at a passing level below the cap")
    if rec.get("platform") != "darwin" or not str(rec.get("python", "")).startswith("3.12"):
        problems.append("the record was not made on darwin, Python 3.12")
    if rec.get("workload") != capacity.workload():
        problems.append("the recorded workload is not the MC-35 names")
    if not re.fullmatch(r"[0-9a-f]{40}", str(rec.get("sha", ""))):
        problems.append("sha is not a commit")
    return problems


@proves_ci
def test_default_matches_measurement_record() -> None:
    default = server_config.TrestleConfig.defaults().max_running_runs
    assert record_problems(record(), default) == []
    assert not hasattr(server_config, "CAPACITY_DEFAULT_PROVISIONAL")  # TM-C3 is removed
    assert server_config.MAX_RUNNING_RUNS_DEFAULT == default
    # queue_depth keeps CL-A1.2's value, final now; it was not measured
    assert server_config.TrestleConfig.defaults().queue_depth == server_config.QUEUE_DEPTH_DEFAULT


@proves_ci
def test_planted_record_defects_are_caught() -> None:
    good = record()
    default = int(good["chosen"])
    assert record_problems(good, default) == []
    assert record_problems(good, default + 1)  # a default that is not chosen
    assert record_problems(dict(good, chosen=default - 1), default - 1)  # not the largest passing
    assert record_problems(dict(good, ratio_bound=capacity.RATIO_BOUND + 1), default)
    assert record_problems(dict(good, platform="linux"), default)
    assert record_problems(dict(good, workload=["spine_leaf"]), default)
    assert record_problems(dict(good, samples=good["samples"][1:]), default)
    survivor = [dict(s) for s in good["samples"]]
    survivor[default - 1]["survivors"] = 1  # a survivor at the chosen N: it was not passing
    assert record_problems(dict(good, samples=survivor), default)
    assert record_problems(dict(good, sha="HEAD"), default)


def test_choice_rule_over_planted_samples() -> None:
    """`chosen` is the largest N with zero misses, zero survivors and p95 within the bound of the
    N = 1 p95; a miss, a survivor or a slow level takes its N out."""

    def sample(n: int, p95: float, misses: int = 0, survivors: int = 0) -> dict[str, Any]:
        return {"n": n, "p95_s": p95, "misses": misses, "survivors": survivors}

    bound = capacity.RATIO_BOUND
    ok = [sample(1, 1.0), sample(2, 1.5), sample(3, bound * 1.0)]
    assert capacity.choose(ok) == 3
    assert capacity.choose([*ok, sample(4, bound * 1.0 + 0.01)]) == 3
    assert capacity.choose([*ok, sample(4, 1.0, misses=1)]) == 3
    assert capacity.choose([*ok, sample(4, 1.0, survivors=1)]) == 3
    assert capacity.choose([sample(1, 1.0, misses=1), sample(2, 1.0)]) == 2  # a level, not a prefix
    assert capacity.choose([sample(1, 1.0, survivors=1)]) is None
    assert capacity.choose([]) is None
    assert capacity.p95([1.0, 2.0, 3.0]) == 3.0
    assert capacity.p95([float(i) for i in range(1, 41)]) == 38.0


def test_miss_rule_over_planted_replies() -> None:
    passed = {"run_id": "r_1", "state": "succeeded", "outcome": {"class": "passed"}}
    assert not capacity.is_miss(passed, 1.0, 120.0)
    assert capacity.is_miss(passed, 121.0, 120.0)  # over its deadline
    assert capacity.is_miss(dict(passed, state="timed_out"), 1.0, 120.0)
    assert capacity.is_miss(dict(passed, outcome={"class": "failed"}), 1.0, 120.0)
    assert capacity.is_miss({"code": "admission.queue_full"}, 0.0, 120.0)  # refused
    assert capacity.is_miss({"run_id": "r_1", "state": "running"}, 60.0, 120.0)
    assert capacity.is_miss(None, 0.0, 120.0)


def _documented() -> dict[str, Any]:
    text = AGENTS.read_text(encoding="utf-8")
    block = re.search(r"<!-- capacity -->(.*?)<!-- /capacity -->", text, re.DOTALL)
    assert block is not None, "docs/agents.md has no capacity line"
    line = " ".join(block.group(1).split())
    running = re.search(r"`max_running_runs` = (\d+) \((not measured|measured)\)", line)
    depth = re.search(r"`queue_depth` = (\d+) \((not measured|measured)\)", line)
    ratio = re.search(r"ratio bound = ([0-9]+(?:[.][0-9]+)?)", line)
    assert running and depth and ratio, line
    return {
        "max_running_runs": int(running.group(1)),
        "running_measured": running.group(2) == "measured",
        "queue_depth": int(depth.group(1)),
        "depth_measured": depth.group(2) == "measured",
        "ratio_bound": float(ratio.group(1)),
    }


@proves_ci
def test_documented_capacity_matches_default() -> None:
    documented = _documented()
    config = server_config.TrestleConfig.defaults()
    assert documented["max_running_runs"] == config.max_running_runs == record()["chosen"]
    assert documented["queue_depth"] == config.queue_depth
    assert documented["ratio_bound"] == capacity.RATIO_BOUND == record()["ratio_bound"]
    # which one was measured: the run capacity, never the queue depth (OQ-10)
    assert documented["running_measured"] and not documented["depth_measured"]
    # the bound is disclosed as a plan default for the maintainer to set
    assert "plan default disclosed for the maintainer to set" in AGENTS.read_text("utf-8")


@proves_ci
def test_queue_beyond_default_fifo() -> None:
    """`default + 1` concurrent fake admissions: `default` hold a slot, exactly one waits; when a
    slot frees the waiting run is dispatched, and later waiters go first in first out."""
    slots = server_config.TrestleConfig.defaults().max_running_runs
    depth = server_config.TrestleConfig.defaults().queue_depth
    started: list[str] = []
    scheduler = Scheduler(max_running=slots, queue_depth=depth)
    scheduler.on_dispatch = lambda order: started.append(order.run_id)
    deadline = time.monotonic() + LONG_DEADLINE_S

    def admit(index: int) -> WorkOrder:
        run_id = f"r_capacity_{index:03d}"
        assert scheduler.check_admit_capacity() is None
        order = scheduler.mint(run_id, "snap", "spec")
        assert scheduler.enqueue(order, deadline).tag == "queued"
        return order

    orders = [admit(i) for i in range(slots + 1)]
    ids = [o.run_id for o in orders]
    assert started == ids[:slots], "the first `default` runs hold the slots, in order"
    assert [w.order.run_id for w in scheduler.waiting] == [ids[slots]], "exactly one is queued"
    late = admit(slots + 1)  # a second waiter, behind the first
    assert [w.order.run_id for w in scheduler.waiting] == [ids[slots], late.run_id]
    scheduler.complete(ids[0])
    assert started == [*ids[: slots + 1]], "the first waiter is dispatched, not the later one"
    scheduler.complete(ids[1])
    assert started == [*ids[: slots + 1], late.run_id], "then the next, first in first out"
    assert not scheduler.waiting
    for run_id in [*ids[2:], late.run_id]:
        scheduler.complete(run_id)


@proves_host
@pytest.mark.host_only
def test_default_holds_slice_a_workload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """At the default capacity the Slice A workload, run concurrently on the host, ends every run
    within its deadline and leaves no MC-13 survivor (the measurement's own zero-miss,
    zero-survivor condition at `chosen`, on a service that has the default and no override)."""
    monkeypatch.delenv("TRESTLE_MAX_RUNNING_RUNS", raising=False)
    monkeypatch.delenv(capacity.DEPTH_ENV, raising=False)
    default = server_config.TrestleConfig.defaults().max_running_runs
    names = capacity.workload()
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=capacity.HOST_TIMEOUT_S) as host:
        capacity.install_workload(host, names)
        sample = capacity.run_level(host, names, default)
    assert sample["n"] == default
    assert sample["misses"] == 0, sample
    assert sample["survivors"] == 0, sample
