"""Provisioning end to end (L.RB-6.3; B3.1, B3.2, WR-ENV-5), shared by the HOST nodes
`host/test_provisioning.py` and their twins `twin/test_provisioning_twin.py`.

The operator's catalog (built by `toolchain_world.World`) lists one test marked `provision`, so the
published reference plugin's tree gains `provision.postgres` (after `backend.postgres`) and the
test node needs it. One MCP call names the test. What is read, for both bindings:

* the lane: how many submits the provisioning node issued (its `submit` tickets), that no run
  released the durable record, and that the test node's first entry follows the provisioning
  node's satisfied end (the postcondition: the authoritative probe saw the record);
* the evidence: the provisioning node's first step is an observation (the probe comes first);
* the store itself, by the caller: exactly one fixture record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trestle_env import tree
from twin import harness
from twin.toolchain_world import World, call

TEST_ID = "demo-provisioned"
TEST_NODE = f"test.{TEST_ID}"
TESTS = [{"id": TEST_ID, "project": "demo-py", "task": "version", "provision": True}]


def world(base: Path) -> World:
    return World(base, installed=True, tests=TESTS)


def run(host: Any, env: str) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    answer = call(host, TEST_ID, env=env)
    assert answer.get("answer", {}).get("outcome") == "passed", answer
    where = harness.run_dir(host, answer["run_id"])
    return answer, harness.lane(where), where


def submits(entries: list[dict[str, Any]]) -> int:
    return sum(
        1
        for e in entries
        if e["class"] == "issue"
        and e.get("path") == tree.PROVISION_UNIT
        and e["effect"] == tree.SUBMIT
    )


def assert_never_released(entries: list[dict[str, Any]]) -> None:
    released = [
        e for e in entries if e["class"] == "released" and e.get("path") == tree.PROVISION_UNIT
    ]
    assert released == [], released


def assert_probe_first(where: Path, entries: list[dict[str, Any]]) -> None:
    """The node's first step is an observation, and its submit ticket comes after it."""
    kinds = [
        e.get("kind")
        for e in harness.events(where)
        if (e.get("payload") or {}).get("path") == tree.PROVISION_UNIT
    ]
    assert kinds and kinds[0] == "step.observed", kinds
    assert "step.action" in kinds and kinds.index("step.observed") < kinds.index("step.action")


def assert_test_after_postcondition(entries: list[dict[str, Any]]) -> None:
    ends = {e["path"]: (n, e) for n, e in enumerate(entries) if e["class"] == "end"}
    at, end = ends[tree.PROVISION_UNIT]
    assert end["condition"] == "satisfied", end
    first = next(n for n, e in enumerate(entries) if e.get("path") == TEST_NODE)
    assert at < first, (at, first)
