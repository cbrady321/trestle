"""The test node starts after every readiness pass, on the fake binding: the CI twin of the HOST
node (L.RB-5.2; MC-B-03).

The same published plugin, the same MCP call naming the catalog's test, on the twins' fake engine
and the real toolchain resolver over the mise-shaped stub. The fact is the HOST node's, read from
the run's lane: the test node's first entry follows both backends' readiness pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle_env import tree
from twin import harness
from twin.toolchain_world import World, call

TEST_ID = "demo-version"
NODE = f"test.{TEST_ID}"


@pytest.mark.stub_proven("WR-VERIFY-2:b-test-after-readiness@stub-twin")
def test_test_starts_after_every_readiness_pass(tmp_path: Path) -> None:
    world = World(tmp_path / "world-dir", installed=True)
    with harness.reference_host(tmp_path / "home", world.environ(tmp_path / "engine.json")) as host:
        answer = call(host, TEST_ID, env="test-after-readiness")
        entries = harness.lane(harness.run_dir(host, answer["run_id"]))
    assert answer["answer"]["outcome"] == "passed", answer["answer"]
    ended = {e["path"]: n for n, e in enumerate(entries) if e["class"] == "end"}
    first = next(n for n, e in enumerate(entries) if e.get("path") == NODE)
    assert entries[first]["class"] == "issue"  # the test's start is its ticket
    for backend in (tree.HTTP_SUPPORT_PATH, tree.POSTGRES_SERVICE):  # the leaves that ran
        assert ended[backend] < first, backend
