"""L.TR-6.2: D6 through the host (MC-12) -- A3.4 and WR-UNIT-1's after-stop variant (SV-7a).

Each run is one MCP `run` call on a `trestle serve` subprocess (`hostpath.mcp_run_tree`, never
MC-26; nothing here imports `tests/proof/harness.py`): the unit called directly, as a root plugin of
its own, and the same unit inside a two-level parent. The two answers are compared as `differ d6`
compares them, after normalizing only V-8's closed list (L-1..L-9): the root answer's account of
the direct unit against the parent answer's account of the node, with the node's lane rows (read
from the host's run directories) alongside, and the node's child view (`await_runs` of the child
handle) equal to the parent answer's account of it (B4-C8).

The after-stop variant: a sibling of the node raises (B1-E6) inside the parent, a whole-root stop.
The two runs are compared only up to the parent's first stop record; the node's account in the
parent is then `stopped`, `unended` or `not_started` (V-8 L-8), and its child view says the same.
The units are `tests/tree/d6_proc.py`'s (real fakes, real child processes), the same as
`tests/tree/test_tr6_equivalence.py`'s PROC variant."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from tests.proof.differ_modes import d6_direct_child as d6
from tests.tree import d6_proc as proc
from tests.tree import hostpath
from trestle.server import answer as answer_mod
from trestle.server.ledger import run_dir_for

HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6
ENV = {"env": "dev"}
NODE = "work"
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")

proves_a34 = pytest.mark.proves("A3.4", "A3.4", "A", "tree", "MCP", "CI")
proves_equiv = pytest.mark.proves(
    "WR-UNIT-1", "WR-UNIT-1:equiv-verdict", "A", "tree", "LOGIC+PROC+MCP", "CI"
)


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory) -> Iterator[mcp_host.McpHost]:
    """One `trestle serve` subprocess with the four plugins of the two pairs published."""
    home = tmp_path_factory.mktemp("d6-host") / "host-home"
    with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as server:
        plugins = server.home / "plugins"
        sources = {
            proc.DIRECT: proc.plugin_source(proc.DIRECT, "direct"),
            proc.CHILD: proc.plugin_source(proc.CHILD, "child"),
        }
        sources[proc.EXCEPTION_DIRECT], sources[proc.EXCEPTION_CHILD] = proc.exception_sources()
        for name, source in sources.items():
            (plugins / f"{name}.py").write_text(source, encoding="utf-8")
        yield server


class HostRun:
    """One MCP `run` call, answered: the wire response, and what `differ d6` reads of it."""

    def __init__(self, server: mcp_host.McpHost, plugin: str) -> None:
        before = server.request_count()
        self.server = server
        self.response = hostpath.mcp_run_tree(server, plugin, ENV)
        self.requests = server.request_count() - before
        assert "code" not in self.response, self.response
        assert self.response["state"] in TERMINAL_STATES, self.response
        self.run_id = str(self.response["run_id"])
        self.answer: dict[str, Any] = self.response["answer"]

    def position(self, path: str) -> d6.Position:
        run_dir = run_dir_for(self.server.home, self.run_id)
        lane = records.lane_rows(run_dir)
        assert not lane.problems and not lane.torn, lane.problems
        entries = [row.entry for row in lane.rows]
        return d6.Position.of(entries, self.answer, path, stop_seq=d6.first_exception_stop(entries))

    def child_view(self, path: tuple[str, ...]) -> dict[str, Any]:
        """The node's child view (V-1.3), by `await_runs` of its handle: its `answer` is the
        node's own account (B4-C8)."""
        handle = answer_mod.child_handle(self.run_id, path)
        views = self.server.call("await_runs", {"run_ids": [handle], "timeout_ms": 1000})
        (view,) = views["result"]
        assert (view["root_run_id"], view["path"]) == (self.run_id, "/".join(path)), view
        return view  # type: ignore[no-any-return]


def _child_view_is_the_roots_account(run: HostRun, path: tuple[str, ...]) -> None:
    view = run.child_view(path)
    account = d6.account_in(run.answer, "/".join(path))
    assert account is not None and view["answer"] == account  # B4-C8: byte-equal


@proves_a34
@proves_equiv
def test_direct_root_call_vs_child_same_account(host: mcp_host.McpHost) -> None:
    direct = HostRun(host, proc.DIRECT)
    child = HostRun(host, proc.CHILD)
    assert (direct.requests, child.requests) == (1, 1)  # one request each, through MC-12
    pair = d6.Pair("host", direct.position(""), child.position(NODE))
    assert d6.check_pair(pair) == []
    # not vacuous: both nodes created, ended satisfied and released what they made
    for position in (pair.direct, pair.child):
        assert [r["class"] for r in position.rows] == [
            "issue",
            "confirmation",
            "end",
            "issue",
            "confirmation",
            "released",
        ]
    # the direct call's answer *is* its root answer: the node reported as the whole answer
    assert direct.answer["outcome"] == "passed" == child.answer["outcome"]
    assert direct.answer["primary"]["listing"] == "rolled_up"
    account = d6.account_in(child.answer, NODE)
    assert account is not None and account["listing"] == "candidate"
    for field in ("node_class", "disposition", "code", "human_action", "resend"):
        assert direct.answer["primary"][field] == account[field], field
    # and the child's view of the node equals the parent answer's account of it (B4-C8)
    _child_view_is_the_roots_account(child, (NODE,))
    _child_view_is_the_roots_account(child, ("prep",))


@proves_a34
@proves_equiv
def test_direct_vs_child_after_sibling_exception_host(host: mcp_host.McpHost) -> None:
    direct = HostRun(host, proc.EXCEPTION_DIRECT)
    child = HostRun(host, proc.EXCEPTION_CHILD)
    assert (direct.requests, child.requests) == (1, 1)
    node = proc.STOPPED_NODE
    pair = d6.Pair("host_after_stop", direct.position(""), child.position(node))
    assert d6.check_pair(pair) == []
    # the parent recorded a whole-root stop (the sibling's uncaught exception, B1-E6) ...
    stop = pair.child.stop_seq
    assert stop is not None
    assert child.answer["outcome"] == "execution_error"
    assert child.answer["primary"]["path"] == ["raiser"]
    assert child.answer["primary"]["code"] == "execution.unit_raised"
    # ... after the node acted (created, confirmed), and the two runs agree up to it
    before = [(r["class"], r.get("effect")) for r in pair.child.rows if r["seq"] < stop]
    assert before == [("issue", "up"), ("confirmation", "up")]
    # the node's account in the parent's answer: stopped (V-8 L-8), and its child view says so
    account = d6.account_in(child.answer, node)
    assert account is not None and account["listing"] in d6.STOPPED_LISTINGS
    _child_view_is_the_roots_account(child, (node,))
    # called directly, no stop reached it: it ran to its own end
    assert direct.answer["primary"]["code"] == "execution.postcondition_timeout"
    assert direct.answer["primary"]["listing"] == "candidate"
