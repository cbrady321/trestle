"""The workflow guarantee suites, parameterized over MC-35 (L.SL-11.1; MC-24, MC-25, MC-35).

A guarantee suite is a check that holds of *any* registered Slice A workflow because it reads only
what the contracts promise and what the MC-35 entry says, never what one fixture does:
`GUARANTEE_SUITES` maps a suite's name to a pure function over `Observed`, the record of one
workflow driven through the MC-12 MCP host, and returns what is wrong (empty: green). A suite that
would pass on an empty record says so (`vacuous`) instead of passing.

`observe(name, entry, directory)` drives the entry once, in the way an agent does: a single
`run(completion="terminal")` call, the identical call re-sent under the same idempotency key, and
one `await_runs` read of the finished run; it also reads the run's lane through the proof court's
own oracle (`tests.proof.records`) and takes a process snapshot. `run_all` then applies every suite.

The workflows are the registry's (`SLICE_A_WORKFLOWS`): the spine fixture and `second_domain_free`
pass the same suites with no runtime change, which is the leaf's claim (`test_conformance_matrix`).
Every timing bound is the harness's or the published clock's (SA-05)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.proof import ancestry, mcp_host, record_facts, records, tolerances
from tests.proof.suites.workflows import published_source
from trestle.common import clock

# the decisive fields of a terminal answer, never omitted (B4-C6: `null`, never absent)
DECISIVE = frozenset(
    {
        "outcome",
        "root_stop",
        "recovered",
        "primary",
        "incomplete",
        "error",
        "cleanup",
        "test_counts",
        "detail",
        "listed_count",
        "unconfirmed_count",
    }
)
CLASSES = frozenset({"passed", "failed", "blocked", "cancelled", "timed_out", "execution_error"})
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 60.0


@dataclass(frozen=True)
class Observed:
    """One workflow driven once: what its calls returned and what it left behind."""

    name: str
    reply: dict[str, Any]  # the one `run(completion="terminal")` call
    resent: dict[str, Any]  # the identical call, re-sent under the same idempotency key
    read: dict[str, Any]  # the finished run, read by one `await_runs`
    run_dirs: tuple[Path, ...]  # every run directory under the host's home
    lane: records.LaneRows
    survivors: tuple[str, ...] = field(default=())  # argv of any process still carrying the run id
    vertices: tuple[str, ...] = ("",)  # the encoded paths of the plan's vertices (the root is "")

    @property
    def answer(self) -> dict[str, Any]:
        answer = self.reply.get("answer")
        return answer if isinstance(answer, dict) else {}


Suite = Callable[[Observed], list[str]]


def one_call_terminal_answer(o: Observed) -> list[str]:
    """B4-C1, B4-C5: the one call returned a terminal answer of exactly one class, decisive fields
    present, the primary a defined account of the root, the cleanup disposition beside it."""
    answer = o.answer
    if not answer:
        return [f"{o.name}: the terminal call returned no answer ({sorted(o.reply)})"]
    problems: list[str] = []
    if missing := sorted(DECISIVE - set(answer)):
        problems.append(f"{o.name}: decisive fields absent: {missing}")
    if answer.get("outcome") not in CLASSES:
        problems.append(f"{o.name}: outcome {answer.get('outcome')!r} is not a class")
    primary = answer.get("primary")
    if not isinstance(primary, dict) or primary.get("path") != []:
        problems.append(f"{o.name}: the primary is not the root's account: {primary!r}")
    if answer.get("outcome") == "passed" and o.reply.get("state") != "succeeded":
        problems.append(f"{o.name}: passed answer over state {o.reply.get('state')!r}")
    cleanup = answer.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("unknown") != 0:
        problems.append(f"{o.name}: cleanup is unknown or absent: {cleanup!r}")
    return problems


def default_run_passes(o: Observed) -> list[str]:
    """A registered workflow run with its default arguments passes: the registry holds workflows
    that work, so a suite green on a workflow that failed would prove nothing about the runtime."""
    if o.answer.get("outcome") != "passed":
        return [f"{o.name}: the default run ended {o.answer.get('outcome')!r}, not passed"]
    return []


def identical_resend_joins_one_execution(o: Observed) -> list[str]:
    """MC-12: the identical call under the same idempotency key joins the run it started: the same
    run id, one run directory, one terminal answer."""
    problems: list[str] = []
    if o.resent.get("run_id") != o.reply.get("run_id"):
        problems.append(f"{o.name}: the re-send got run {o.resent.get('run_id')!r}, not the first")
    if len(o.run_dirs) != 1:
        problems.append(f"{o.name}: {len(o.run_dirs)} run directories after two identical calls")
    if o.resent.get("answer") != o.reply.get("answer"):
        problems.append(f"{o.name}: the re-send's answer differs from the first call's")
    return problems


def lane_record_facts(o: Observed) -> list[str]:
    """MC-19..21: the lane is readable (no torn or foreign entry), holds one plan entry first and
    one `NodeEnd` for the root and none for any path but the plan's vertices (a vertex the run
    never started, such as an alternative a choice left out, has none), and every effect entry has
    its own claim before it. A one-vertex plan has exactly one."""
    lane = o.lane
    if lane.problems or lane.torn:
        return [f"{o.name}: unreadable lane: {list(lane.problems) or 'torn'}"]
    if not lane.rows:
        return [f"{o.name}: the lane holds no entry: nothing is proven (vacuous)"]
    problems: list[str] = []
    classes = [row.cls for row in lane.rows]
    if classes[0] != "plan":
        problems.append(f"{o.name}: the lane opens with {classes[0]!r}, not the plan entry")
    ends = [row.path for row in lane.rows if row.cls == "end"]
    if len(ends) != len(set(ends)) or "" not in ends:
        problems.append(f"{o.name}: NodeEnd entries {ends} are not one per vertex with the root's")
    if len(o.vertices) == 1 and len(ends) != 1:
        problems.append(f"{o.name}: {len(ends)} NodeEnd entries for one vertex")
    if unknown := sorted(set(ends) - set(o.vertices)):
        problems.append(f"{o.name}: NodeEnd entries for paths that are not vertices: {unknown}")
    verdict = record_facts.claim_before_effect(lane)
    if not verdict.ok:
        problems.append(f"{o.name}: {'; '.join(verdict.violations)}")
    return problems


def read_matches_answer(o: Observed) -> list[str]:
    """B4-C1: the answer is a pure projection over durable inputs, so reading the finished run
    gives the answer the terminal call gave."""
    views = o.read.get("result", o.read)
    view = views[0] if isinstance(views, list) and views else views
    if not isinstance(view, dict) or view.get("answer") != o.reply.get("answer"):
        return [f"{o.name}: the run read back differs from the terminal call's answer"]
    return []


def no_process_left(o: Observed) -> list[str]:
    """V-2.3: once the answer is terminal no process of the run is alive."""
    return [f"{o.name}: a process of the run is alive: {argv[:80]}" for argv in o.survivors]


GUARANTEE_SUITES: dict[str, Suite] = {
    "one_call_terminal_answer": one_call_terminal_answer,
    "default_run_passes": default_run_passes,
    "identical_resend_joins_one_execution": identical_resend_joins_one_execution,
    "lane_record_facts": lane_record_facts,
    "read_matches_answer": read_matches_answer,
    "no_process_left": no_process_left,
}


def observe(name: str, entry: dict[str, Any], directory: Path) -> Observed:
    """Drive `entry` once through the MCP host (nothing about the workflow is assumed but its
    MC-35 entry: the fixture file, its `env_arg`, its declared codes)."""
    args: dict[str, Any] = {}
    if entry["env_arg"] is not None:
        args[str(entry["env_arg"])] = "dev"
    call = {
        "plugin": name,
        "args": args,
        "wait_ms": int(tolerances.HARNESS_WAIT_MS),
        "completion": "terminal",
        "idempotency_key": f"guarantee-{name}",
    }
    with mcp_host.McpHost(home=directory / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        (host.home / "plugins" / f"{name}.py").write_text(published_source(entry), encoding="utf-8")
        reply = host.call("run", call)
        resent = host.call("run", call)
        assert isinstance(reply, dict) and isinstance(resent, dict), (reply, resent)
        read = host.call(
            "await_runs",
            {
                "run_ids": [reply["run_id"]],
                "mode": "all",
                "timeout_ms": tolerances.HARNESS_WAIT_MS,
            },
        )
        run_dirs = tuple(sorted((host.home / "runs").glob("*/r_*")))
        lane = records.lane_rows(run_dirs[0]) if run_dirs else records.LaneRows()
        run_id = str(reply["run_id"])
        survivors = tuple(p.argv for p in ancestry.snapshot() if run_id in p.argv)
        vertices: tuple[str, ...] = ("",)
        if run_dirs:
            spec = json.loads((run_dirs[0] / "evidence" / "spec.json").read_text(encoding="utf-8"))
            vertices = tuple(str(v["path"]) for v in spec["plan"]["vertices"])
        return Observed(
            name,
            reply,
            resent,
            read if isinstance(read, dict) else {"result": read},
            run_dirs,
            lane,
            survivors,
            vertices,
        )


def run_all(name: str, entry: dict[str, Any], directory: Path) -> dict[str, list[str]]:
    """Every guarantee suite over `entry`, by name: an empty list is a green suite."""
    observed = observe(name, entry, directory)
    return {suite: check(observed) for suite, check in GUARANTEE_SUITES.items()}
