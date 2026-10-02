"""L.SL-2.2: `describe_plugin` discloses the longest a terminal call is held
(`max_call_duration_s` = admitted deadline + `clock.finalization_margin`) and, for a workflow
entry, its declared outcome codes; every existing key stays, and `tools/list` is byte-identical
whatever number of workflows is registered. Checked through the real server over the MC-12 host."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.proof.mcp_host import McpHost
from trestle.common import clock

DEADLINE_S = 60

WORKFLOW_SOURCE = """
from __future__ import annotations

from datetime import timedelta

from trestle.plugin import Context, trestle
from trestle.workflow import (
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)


class Unit:
    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit="unit",
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=30)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset({"net.flaky", "tool.busy"}),
            remedies=(),
            budget=timedelta(seconds=30),
            max_attempts=1,
        )


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=DEADLINE))


@trestle(deadline=timedelta(seconds=DEADLINE))
def NAME(ctx: Context, name: str = "x") -> dict[str, str]:
    return {"name": name}
"""

# The keys `describe_plugin` returned before this leaf (WR-COMPAT-9): all must survive.
EXISTING_KEYS = {
    "name",
    "version",
    "snapshot_id",
    "source_sha256",
    "summary_budget",
    "timeout_s",
    "deadline_s",
    "deadline_source",
    "input_schema",
    "return_schema",
}


def _home_with_workflows(root: Path, label: str, count: int) -> Path:
    home = root / label
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    for i in range(count):
        name = f"wf_{i:02d}"
        source = WORKFLOW_SOURCE.replace("NAME", name).replace("DEADLINE", str(DEADLINE_S))
        (plugins / f"{name}.py").write_text(source, encoding="utf-8")
    return home


def _tools_bytes(host: McpHost) -> bytes:
    tools = json.loads(host.tools_list_raw())["result"]["tools"]
    return json.dumps(tools, sort_keys=True, separators=(",", ":")).encode("utf-8")


@pytest.mark.proves("WR-TERM-8", "WR-TERM-8:describe-duration-codes", "A", "single", "MCP", "CI")
def test_describe_reports_deadline_plus_margin_and_codes(tmp_path: Path) -> None:
    home = _home_with_workflows(tmp_path, "home", 1)
    with McpHost(home=home) as host:
        workflow = host.call("describe_plugin", {"plugin_id": "wf_00"})
        plain = host.call("describe_plugin", {"plugin_id": "echo"})

    # a workflow entry: the admitted deadline plus the one finalization margin, and its codes
    assert EXISTING_KEYS <= set(workflow)
    assert workflow["deadline_s"] == float(DEADLINE_S)
    assert workflow["max_call_duration_s"] == DEADLINE_S + clock.finalization_margin
    assert workflow["declared_codes"] == ["net.flaky", "tool.busy"]

    # a plain plugin: same duration rule over its default deadline, and no code list
    assert EXISTING_KEYS <= set(plain)
    assert plain["max_call_duration_s"] == plain["deadline_s"] + clock.finalization_margin
    assert "declared_codes" not in plain


def test_tools_list_size_independent_of_workflow_count(tmp_path: Path) -> None:
    sizes: dict[int, bytes] = {}
    for count in (1, 20):
        home = _home_with_workflows(tmp_path, f"home{count}", count)
        with McpHost(home=home) as host:
            listed = host.call("list_plugins")
            names = {p["name"] for p in listed["items"]}
            assert {f"wf_{i:02d}" for i in range(count)} <= names
            sizes[count] = _tools_bytes(host)
    assert sizes[1] == sizes[20]
    assert len(sizes[1]) <= 8192
