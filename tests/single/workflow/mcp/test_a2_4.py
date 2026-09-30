"""L.SL-7.2: A2.4 through the MC-12 MCP host, the calls an agent makes.

"An action with no declared contract, or a plan with an uncovered precondition, refused before any
mutation." Both variants are one call each. A contract with an element missing is refused at
`publish_plugin` (B1-E1, L.SL-7.1): no snapshot, no plugin, no run. A declared precondition the
node's own `observe` carries no check for is stopped in-node by the loop before the first claim
(DM-34, design U7): one `run(completion="terminal")` call answers it with the stable code
`execution.plan_precondition_uncovered`, zero claim entries in the lane, and exactly one
decision-table class, never `passed`. Only the first variant's source differs from the spine
fixture (`tests/fixtures/workflows/spine_leaf.py`), by one declared field.

The lane is read through the proof court's own oracle (`tests.proof.records`); every timing bound
comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from trestle.common import clock

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "workflows"
FIXTURE = "spine_leaf"
UNCOVERED = "execution.plan_precondition_uncovered"
CONTRACT_MISSING = "publication.plan_contract_missing"
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 120.0

_DECLARED = '    preconditions=(),\n    postcondition="ready",\n'
_UNCOVERED = '    preconditions=("toolchain",),\n    postcondition="ready",\n'
_NO_CONTRACT = '    preconditions=(),\n    postcondition="",\n'


def _variant(declared: str) -> str:
    source = (FIXTURES / f"{FIXTURE}.py").read_text(encoding="utf-8")
    assert source.count(_DECLARED) == 1, "the spine fixture's declaration moved"
    return source.replace(_DECLARED, declared)


@contextmanager
def _host(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        yield host


def _runs(host: mcp_host.McpHost) -> list[Path]:
    return sorted((host.home / "runs").glob("*/*")) if (host.home / "runs").exists() else []


def _no_contract(host: mcp_host.McpHost) -> None:
    refused = host.call("publish_plugin", {"source": _variant(_NO_CONTRACT)})
    assert refused["code"] == CONTRACT_MISSING, refused
    assert "postcondition" in refused["message"], refused
    answer = host.call("run", {"plugin": FIXTURE, "args": {"env": "dev"}, "wait_ms": 0})
    assert answer.get("run_id") is None and answer.get("state") != "succeeded", answer
    assert _runs(host) == [], "a refused contract left a run behind"
    listed = host.call("list_plugins", {})
    assert FIXTURE not in {p["name"] for p in listed["items"]}, listed


def _uncovered_precondition(host: mcp_host.McpHost) -> None:
    (host.home / "plugins" / f"{FIXTURE}.py").write_text(_variant(_UNCOVERED), encoding="utf-8")
    answer = host.call(
        "run",
        {
            "plugin": FIXTURE,
            "args": {"env": "dev", "mode": "advance"},
            "wait_ms": int(tolerances.HARNESS_WAIT_MS),
            "completion": "terminal",
        },
    )
    assert isinstance(answer, dict), answer
    # exactly one class, read from the B4 answer (the S0 `outcome.class` field is the plugin
    # callable's own return and still says `passed`: a known SV-4.2 wart, not read here)
    verdict = answer["answer"]
    assert verdict["outcome"] == "failed", answer
    assert verdict["primary"]["node_class"] == "failed", answer
    assert verdict["primary"]["code"] == UNCOVERED, answer
    assert verdict["primary"]["human_action"] is None and verdict["error"] is None, answer
    (run_dir,) = [p for p in _runs(host) if p.name == answer["run_id"]]
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    classes = [row.cls for row in lane.rows]
    assert "issue" not in classes and "confirmation" not in classes, classes  # zero claim entries
    ends = [row.entry for row in lane.rows if row.cls == "end"]
    assert len(ends) == 1 and ends[0]["code"] == UNCOVERED, ends


VARIANTS: dict[str, Any] = {
    "no-declared-contract": _no_contract,
    "uncovered-precondition": _uncovered_precondition,
}


@pytest.mark.proves(
    "WR-PLAN-12", "WR-PLAN-12:precondition-single-vertex", "A", "single", "LOGIC", "CI"
)
@pytest.mark.proves("WR-PLAN-12", "A2.4", "A", "single", "LOGIC+MCP", "CI")
@pytest.mark.parametrize("variant", list(VARIANTS))
def test_refused_before_mutation(variant: str, tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        VARIANTS[variant](host)
