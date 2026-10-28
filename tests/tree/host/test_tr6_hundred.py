"""L.TR-6.4: the 100-node answer, through one MCP call, stays within the answer budget (A1.6).

`generators.hundred_node(100)` is one root over a hundred sibling leaves (concurrency 2); it is in
`generators.GENERATED` and so in `plans.valid_plans`, and its declaration is published unchanged
(A2c4-6). Only its units are made real: the generated source's declaration-only `Unit` is replaced
by one that is ready at its first observation, except (mode `fail`) node `FAILING`, whose
`advance` fails it; and its plugin function calls `run_tree`. Nothing else in the source changes
(the test compares the declaration block byte for byte).

Each variant is one MCP `run` call (MC-12; `completion="terminal"`) on a `trestle serve`
subprocess. B4-C6 bounds the `answer` field of the reply by the run's own `SUMMARY_BUDGET`:

* the decisive fields (`outcome`, `root_stop`, `recovered`, `primary`, `cleanup`, `test_counts`,
  `error`, `incomplete`, `detail`, and the counts of `listed` and `unconfirmed`) are always
  inline, whole, and equal to the ones in the full answer;
* `listed` fills in key order, a prefix, and what does not fit is behind `detail`
  (`<run_id>/answer`), which resolves through `fetch` to the full answer, whose `listed` is all a
  hundred vertices;
* every node's handle resolves (`await_runs` of the child handle, V-1.3) to that node's account in
  the full answer (B4-C8);
* today's result summary is untouched: it is what the plugin returned, within the budget the
  run's own spec carries, and the reply grows by no more than the budget."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.fixtures.trees import generators
from tests.proof import mcp_host, tolerances
from tests.tree import plans
from trestle.common.plan import bounds
from trestle.server import answer as answer_mod
from trestle.server.ledger import run_dir_for

HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 12
WAIT_MS = int(tolerances.JOIN_WAIT_S * 1000 * 6)
FAILING = 42
NODES = 100
DECISIVE = (
    "outcome",
    "root_stop",
    "recovered",
    "primary",
    "cleanup",
    "test_counts",
    "error",
    "incomplete",
    "detail",
    "listed_count",
    "unconfirmed_count",
)

proves_a16 = pytest.mark.proves("A1.6", "A1.6", "A", "tree", "MCP", "CI")
proves_budget = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:hundred-node-budget", "A", "tree", "LOGIC+MCP", "CI"
)
proves_size = pytest.mark.proves("WR-TERM-5", "WR-TERM-5:tree-size", "A", "tree", "MCP", "CI")

TREE = generators.hundred_node(NODES)

_UNIT = '''class Unit:
    """A leaf of the hundred-node tree: ready at its first observation, except node FAILING in
    mode `fail`, which `advance` fails (an ordinary failure: nothing depends on it)."""

    def __init__(self, declaration: LeafDeclaration) -> None:
        self._declaration = declaration

    def declare(self) -> LeafDeclaration:
        return self._declaration

    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:
        ready = not (RUN["mode"] == "fail" and params["index"] == FAILING)
        return Observation(
            present=ready,
            selector_present=ready,
            identity_proven=True,
            configuration_compatible=True,
            postcondition=CheckResult(ready, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        return Failed("fixture.node_failed", f"node {params['index']:03d} is unhealthy")

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError("a hundred-node leaf creates nothing")


'''
_STRUCTURAL_ENTRY = (
    f'def {TREE.name}(ctx: Context) -> dict[str, str]:\n    return {{"fixture": "{TREE.name}"}}\n'
)
_RUNNING_ENTRY = (
    f'def {TREE.name}(ctx: Context, mode: str = "pass") -> dict[str, str]:\n'
    '    RUN["mode"] = mode\n'
    "    run_tree(ctx, ENTRY, {}, ports={})\n"
    f'    return {{"fixture": "{TREE.name}", "mode": mode}}\n'
)


def declaration_block(source: str) -> str:
    """The `ENTRY = WorkflowEntry(...)` block: what admission compiles and the tree's identity."""
    return source[source.index("ENTRY = WorkflowEntry(") : source.index("@trestle(")]


def running_source(tree: generators.Tree) -> str:
    """`tree`'s generated source with real units and a plugin function that walks the tree; its
    declaration block is untouched."""
    source = tree.source
    start, end = source.index("class Unit:"), source.index("ENTRY = WorkflowEntry(")
    source = source[:start] + _UNIT + source[end:]
    anchor = "from trestle.plugin import Context, trestle\n"
    assert source.count(anchor) == 1
    source = source.replace(
        anchor,
        anchor
        + "from trestle.workflow.loop import run_tree\n"
        + "from trestle.workflow.units import Failed\n"
        + "from trestle.workflow.values import CheckResult, Observation\n",
    )
    assert source.count("LABEL = ") == 1
    source = source.replace(
        "LABEL = ", f"FAILING = {FAILING}\nRUN: dict[str, Any] = {{'mode': 'pass'}}\nLABEL = "
    )
    assert source.count(_STRUCTURAL_ENTRY) == 1
    return source.replace(_STRUCTURAL_ENTRY, _RUNNING_ENTRY)


def _encoded(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class Answered:
    """One MCP `run` call, answered: the reply, its `answer`, the full answer behind `detail`."""

    def __init__(self, server: mcp_host.McpHost, mode: str) -> None:
        before = server.request_count()
        reply = server.call(
            "run",
            {
                "plugin": TREE.name,
                "args": {"mode": mode},
                "wait_ms": WAIT_MS,
                "completion": "terminal",
            },
        )
        assert isinstance(reply, dict) and "code" not in reply, reply
        assert server.request_count() - before == 1  # one MCP call
        self.server = server
        self.mode = mode
        self.reply: dict[str, Any] = reply
        self.run_id = str(reply["run_id"])
        self.answer: dict[str, Any] = reply["answer"]
        self.run_dir = run_dir_for(server.home, self.run_id)
        self.spec = json.loads((self.run_dir / "evidence" / "spec.json").read_text("utf-8"))

    def full(self) -> dict[str, Any]:
        """The full, unbudgeted answer, resolved from `detail` through `fetch` (its one line)."""
        fetched = self.server.call(
            "fetch", {"target": self.answer["detail"], "window": {"kind": "head", "count": 1}}
        )
        assert fetched.get("tag") == "text" and not fetched.get("truncated"), fetched
        (line,) = fetched["lines"]
        full: dict[str, Any] = json.loads(line)
        return full

    def node_views(self) -> dict[str, dict[str, Any]]:
        """Every node's child view, one `await_runs` call over all the handles (V-1.3)."""
        handles = {
            answer_mod.child_handle(self.run_id, (f"n{i:03d}",)): f"n{i:03d}" for i in range(NODES)
        }
        views = self.server.call(
            "await_runs",
            {"run_ids": list(handles), "timeout_ms": int(tolerances.JOIN_WAIT_S * 100)},
        )
        assert isinstance(views, dict) and isinstance(views["result"], list), views
        by_path = {view["path"]: view for view in views["result"]}
        assert set(by_path) == set(handles.values()), sorted(by_path)[:5]
        return by_path


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory) -> Iterator[mcp_host.McpHost]:
    """One `trestle serve` subprocess with the hundred-node tree published."""
    home = tmp_path_factory.mktemp("hundred-host") / "host-home"
    with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as server:
        (server.home / "plugins" / f"{TREE.name}.py").write_text(
            running_source(TREE), encoding="utf-8"
        )
        yield server


def test_the_tree_is_the_generated_one_and_its_declaration_is_unchanged() -> None:
    """What is published is `hundred_node(100)`: in `GENERATED`, in `valid_plans` (A2c4-6), 101
    vertices, and its declaration block is the generated source's, byte for byte."""
    assert TREE in generators.GENERATED
    assert TREE.name in {p.name for p in plans.valid_plans("nonchoice")}
    assert TREE.vertices == NODES + 1
    assert declaration_block(running_source(TREE)) == declaration_block(TREE.source)
    assert running_source(TREE) != TREE.source  # the units, the function: the only difference


def _check_bounded(run: Answered, full: dict[str, Any]) -> None:
    answer = run.answer
    budget = int(run.spec["summary_budget"])
    assert budget == bounds.SUMMARY_BUDGET_DEFAULT  # the run's own spec: nothing raised for a tree
    assert _encoded(answer) <= budget  # the TerminalAnswer field alone, on the encoded reply
    # the decisive fields: all present (null, never omitted), whole, and the full answer's own
    assert set(DECISIVE) <= set(answer), set(DECISIVE) - set(answer)
    for key in DECISIVE:
        assert answer[key] == full[key], key
    assert answer["primary"]["path"] == full["primary"]["path"]
    # `listed`: the count is the full count, the inline entries a prefix of the full list in key
    # order, and what does not fit is behind `detail` (not silently dropped)
    assert answer["listed_count"] == len(full["listed"]) == NODES  # 101 vertices less `primary`
    inline = answer["listed"]
    assert 0 < len(inline) < NODES  # the budget does bind on a hundred nodes
    assert inline == full["listed"][: len(inline)]
    assert answer["unconfirmed_count"] == len(full["unconfirmed"]) == 0
    assert answer["detail"] == answer_mod.detail_handle(run.run_id)
    paths = [tuple(node["path"]) for node in full["listed"]]
    vertices = {(), *((f"n{i:03d}",) for i in range(NODES))}  # the root and a hundred leaves
    assert sorted(paths) == sorted(vertices - {tuple(answer["primary"]["path"])})  # each once
    behind = full["listed"][len(inline) :]
    assert len(inline) + len(behind) == NODES and behind  # the rest is what `detail` resolves
    # the reply grows by the budget at most: `answer` is one additive field (B4-C6)
    without = {key: value for key, value in run.reply.items() if key != "answer"}
    assert _encoded(run.reply) - _encoded(without) <= budget + len('"answer":,')
    # today's result summary and its budget are unchanged: what the plugin returned, within the
    # run's own budget, projected as it always was
    assert run.reply["summary"] == {"fixture": TREE.name, "mode": run.mode}
    assert _encoded(run.reply["summary"]) <= budget
    assert run.reply["truncated"] is False and "omitted" not in run.reply


def _check_handles(run: Answered, full: dict[str, Any]) -> None:
    """Every per-node handle fetches: its child view's answer is the full answer's account."""
    accounts = {"/".join(node["path"]): node for node in (full["primary"], *full["listed"])}
    views = run.node_views()
    assert len(views) == NODES
    for path, view in views.items():
        assert (view["root_run_id"], view["path"]) == (run.run_id, path)
        assert view["answer"] == accounts[path], path  # B4-C8: byte-equal to the root's account
        assert view["state"] == run.reply["state"], path


@proves_a16
@proves_budget
@proves_size
def test_hundred_node_answer_within_budget_handles_fetch(host: mcp_host.McpHost) -> None:
    """Pass and fail variants, one MCP call each: the encoded answer is within the run's own
    `SUMMARY_BUDGET`, every decisive field is present and untruncated, `primary` and its path are
    present, the count of `listed` is the full count, the entries past the budget are behind
    `detail` (which resolves through `fetch`), every per-node handle fetches, and today's result
    summary and its budget are unchanged."""
    passed = Answered(host, "pass")
    failed = Answered(host, "fail")
    for run in (passed, failed):
        full = run.full()
        _check_bounded(run, full)
        _check_handles(run, full)
    # pass: the root rolled up, passed, every leaf a candidate and satisfied
    assert passed.answer["outcome"] == "passed"
    assert passed.answer["primary"]["path"] == [] and passed.answer["primary"]["listing"] == (
        "rolled_up"
    )
    leaves = [node for node in passed.full()["listed"] if node["path"]]
    assert len(leaves) == NODES and {node["condition"] for node in leaves} == {"satisfied"}
    # fail: the failing leaf is the primary, by path and code, and no other leaf is failed
    assert failed.answer["outcome"] == "failed"
    assert failed.answer["primary"]["path"] == [f"n{FAILING:03d}"]
    assert failed.answer["primary"]["code"] == "fixture.node_failed"
    assert failed.answer["primary"]["node_class"] == "failed"
    others = [
        node
        for node in failed.full()["listed"]
        if node["path"] and node["condition"] != "satisfied"
    ]
    assert others == []  # every other leaf is satisfied; the root is rolled up as failed
    # and the bound is the same for both: the answer is no bigger for the failure
    assert _encoded(failed.answer) <= int(failed.spec["summary_budget"])
    # both are the same size class of answer: the fetched full answer counts the same hundred
    assert passed.full()["listed_count"] == failed.full()["listed_count"] == NODES
