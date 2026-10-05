"""L.TR-6.8: the run-capacity measurement over the Slice A workload (MC-B3-10; OQ-10, DM-30).

    python -m tests.tree.capacity measure [--out PATH] [--cap N]

runs the MC-35 workload (`tests.proof.suites.workflows.SLICE_A_WORKFLOWS`: every entry, the tree
workflows included) on one `trestle serve` at N = 1, 2, ... concurrent runs until the first failing
N (cap `4 x cpu_count`) and writes `capacity_slice_a.json` beside this module:

    {sha, platform, python, workload, samples: [{n, p95_s, misses, survivors}], ratio_bound, chosen}

It is a HOST step (the measured machine is the point, CSC-9): the caller holds the CSC-12 lock and
has re-pointed the clean host venv at this worktree (`tests.proof.host.host_lock.hold`).

A level N is `len(workload)` waves of N runs started together, so every entry runs N times and at
most N runs are in flight; wave `w` starts entry `(i + w) mod len(workload)` as its `i`th run, so
each wave mixes the workload and each environment key is used once (WR-OWN-8: one running run per
key). A run's time is the wall time from its request to its terminal answer. Each entry runs once
before the first level and is not measured: the first run of a workflow on a fresh server pays for
its publication and first import, which would inflate the N = 1 baseline the bound is taken from.

* a *miss* is a run that did not end `passed` within its declared deadline: a refusal, a run
  still running after the wait, a state other than `succeeded`, an outcome other than `passed`, or
  a time over the deadline;
* a *survivor* is a process still carrying a run id of the level once every answer is in (MC-13,
  V-2.3);
* a level *fails* when it has a miss or a survivor, or its p95 run time exceeds `RATIO_BOUND` times
  the N = 1 p95 (the first level defines the baseline and only fails on a miss or a survivor);
* `chosen` is the largest N with zero misses, zero survivors and p95 within the bound.

`RATIO_BOUND` is the measurement's own degradation bound (a plan default disclosed for the
maintainer to set in docs/agents.md, as the reserve of DM-46 is): a p95 run-time ratio. It is not
MC-09's append-cost ratio (`clock.APPEND_COST_RATIO`, a throughput ratio) and not a stop bound, so
it is defined here and not in `trestle.common.clock` (DM-15, A2c2-11)."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from tests.proof import ancestry, mcp_host, tolerances
from tests.proof.suites.workflows import SLICE_A_WORKFLOWS, published_source

RATIO_BOUND = 2.0
RECORD = Path(__file__).with_name("capacity_slice_a.json")
REPO = Path(__file__).resolve().parents[2]
# a run that outlives this wait is a miss (the host's own timeout is longer)
WAIT_S = tolerances.JOIN_WAIT_S * 6
HOST_TIMEOUT_S = WAIT_S * 2
CAPACITY_ENV = "TRESTLE_MAX_RUNNING_RUNS"
DEPTH_ENV = "TRESTLE_QUEUE_DEPTH"


def workload() -> list[str]:
    """The MC-35 workflow names, in a fixed order."""
    return sorted(SLICE_A_WORKFLOWS)


def cap_default() -> int:
    """The measurement's cap: four runs per CPU."""
    return 4 * (os.cpu_count() or 1)


def p95(times: list[float]) -> float:
    """The 95th percentile, nearest rank (the largest of fewer than twenty runs)."""
    ordered = sorted(times)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def deadline_s(name: str) -> float:
    """The workflow's declared deadline in seconds (its fixture's `@trestle(deadline=)`)."""
    source = published_source(SLICE_A_WORKFLOWS[name])
    match = re.search(r"@trestle\([^)]*deadline=(\d+)", source)
    assert match is not None, f"{name}: no declared deadline"
    return float(match.group(1))


def install_workload(host: mcp_host.McpHost, names: list[str]) -> None:
    """Publish every workflow of the workload into the host's plugin directory."""
    for name in names:
        (host.home / "plugins" / f"{name}.py").write_text(
            published_source(SLICE_A_WORKFLOWS[name]), encoding="utf-8"
        )


def _request(name: str, key: str) -> dict[str, Any]:
    entry = SLICE_A_WORKFLOWS[name]
    args: dict[str, Any] = {}
    if entry["env_arg"] is not None:
        args[str(entry["env_arg"])] = key
    return {
        "plugin": name,
        "args": args,
        "wait_ms": int(WAIT_S * 1000),
        "completion": "terminal",
        "idempotency_key": f"capacity-{name}-{key}",
    }


def is_miss(reply: Any, elapsed: float, deadline: float) -> bool:
    """Whether a run's reply and time make it a miss (see the module docstring)."""
    if not isinstance(reply, dict) or "run_id" not in reply or "code" in reply:
        return True
    outcome = reply.get("outcome")
    passed = isinstance(outcome, dict) and outcome.get("class") == "passed"
    return not (reply.get("state") == "succeeded" and passed and elapsed <= deadline)


def run_wave(
    host: mcp_host.McpHost, names: list[str], n: int, wave: int, tag: str
) -> tuple[list[float], int, list[str]]:
    """`n` runs started together, run `i` of workflow `(i + wave) mod len(names)`; returns the run
    times, the number of misses and the run ids. `tag` makes every run's idempotency key and
    environment key its own: a repeated key would join the run it started, not start one."""
    sent: list[tuple[str, int, float]] = []
    for i in range(n):
        name = names[(i + wave) % len(names)]
        request = _request(name, f"{tag}w{wave}i{i}")
        sent.append((name, host.hold("run", request), time.monotonic()))
    times = [0.0] * n
    replies: list[Any] = [None] * n

    def collect(i: int) -> None:
        _, request_id, started = sent[i]
        try:
            replies[i] = host.join(request_id, timeout=HOST_TIMEOUT_S)
        except Exception as error:  # a torn or timed-out call is a miss, not a crash
            replies[i] = {"code": f"harness.{type(error).__name__}"}
        times[i] = time.monotonic() - started

    threads = [threading.Thread(target=collect, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    deadlines = {name: deadline_s(name) for name in names}
    misses = sum(is_miss(replies[i], times[i], deadlines[sent[i][0]]) for i in range(n))
    run_ids = [str(r["run_id"]) for r in replies if isinstance(r, dict) and "run_id" in r]
    return times, misses, run_ids


def survivors(run_ids: list[str]) -> int:
    """Processes still carrying one of the run ids (MC-13)."""
    ids = set(run_ids)
    return sum(1 for p in ancestry.snapshot() if any(run_id in p.argv for run_id in ids))


def run_level(host: mcp_host.McpHost, names: list[str], n: int) -> dict[str, Any]:
    """The workload at `n` concurrent runs: every entry `n` times over `len(names)` waves."""
    times: list[float] = []
    misses = 0
    run_ids: list[str] = []
    for wave in range(len(names)):
        wave_times, wave_misses, wave_ids = run_wave(host, names, n, wave, f"n{n}")
        times += wave_times
        misses += wave_misses
        run_ids += wave_ids
    return {
        "n": n,
        "p95_s": round(p95(times), 3),
        "misses": misses,
        "survivors": survivors(run_ids),
    }


def passes(sample: dict[str, Any], baseline_p95: float, ratio_bound: float = RATIO_BOUND) -> bool:
    """A level's own three conditions: no miss, no survivor, p95 within the bound."""
    return bool(
        sample["misses"] == 0
        and sample["survivors"] == 0
        and sample["p95_s"] <= ratio_bound * baseline_p95
    )


def choose(samples: list[dict[str, Any]], ratio_bound: float = RATIO_BOUND) -> int | None:
    """The largest N of `samples` that passes; None when none does."""
    if not samples:
        return None
    baseline = float(samples[0]["p95_s"])
    ok = [int(s["n"]) for s in samples if passes(s, baseline, ratio_bound)]
    return max(ok) if ok else None


def measure(cap: int | None = None) -> dict[str, Any]:
    """N = 1, 2, ... until the first failing N or the cap, on one host with room for the cap."""
    names = workload()
    top = cap if cap is not None else cap_default()
    os.environ[CAPACITY_ENV] = str(top)
    os.environ[DEPTH_ENV] = str(top * len(names))
    samples: list[dict[str, Any]] = []
    with mcp_host.McpHost(timeout_s=HOST_TIMEOUT_S) as host:
        install_workload(host, names)
        for wave in range(len(names)):  # warm-up: each entry once, not measured (first-use cost)
            run_wave(host, names, 1, wave, "warm")
        baseline = 0.0
        for n in range(1, top + 1):
            sample = run_level(host, names, n)
            samples.append(sample)
            print(f"capacity: {sample}", flush=True)
            if n == 1:
                baseline = float(sample["p95_s"])
            if not passes(sample, baseline):
                break
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "sha": sha,
        "platform": sys.platform,
        "python": platform.python_version(),
        "workload": names,
        "samples": samples,
        "ratio_bound": RATIO_BOUND,
        "chosen": choose(samples),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.tree.capacity")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("measure", help="measure the Slice A workload and write the record")
    run.add_argument("--out", type=Path, default=RECORD)
    run.add_argument("--cap", type=int, default=None)
    args = parser.parse_args(argv)
    record = measure(args.cap)
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"capacity: chosen {record['chosen']} -> {args.out}")
    return 0 if record["chosen"] is not None else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
