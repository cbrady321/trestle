"""L.TR-L.7: WR-UNIT-5's five terminal paths through the real host (J-TRL T1, A8.4).

`tests/tree/test_tr4_unit5_lib.py` proved each path in-library: child `c` of `creator_pk` creates
a process `P` and a resource `K`, the tree ends (a failing sibling, `c`'s own exception, a root
cancel, the root deadline, a server restart) and nothing `c` made may outlive the terminal answer.
Here the same tree runs on a `trestle serve` subprocess through the MCP `run` tool (MC-12, MC-B3-02:
no MC-26 harness, no in-process kernel), and the same things are read from outside: the process
table before and after (MC-13), the fake's inventory, the run's ledger and lane, and the wire
answer.

The restart variant is real: the server is SIGKILLed with the run live, restarted on the same home
(MC-12 `kill_server()` / `restart()`), and recovery has to end `P` and `K` and finalize the run.

`P` and `K` are both run-lifetime `FakeMarker` processes (`InRunGroup` release): the server's
`OperatorLimits.release_executables` cannot be loaded from `config.toml` today, so a real host can
never run an `ArgvRelease` (gap recorded in the RETURN). The deadline variant shortens the server's
own clocks through its environment (`TRESTLE_RELEASE_SLICE_S`, `TRESTLE_FINALIZATION_RESERVE_S`) and
the fixture's budgets by exact-string replacement, as the in-library variant does."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker, run_scoped_selector

from tests.core.spine import support
from tests.proof import ancestry, mcp_host, records, tolerances
from tests.tree import hostpath
from tests.tree import treekit as tk
from trestle.common import clock, codes
from trestle.server.ledger import TERMINAL_KINDS, evidence_dir

P_EFFECT, K_EFFECT, STOP_EFFECT = "spawn_p", "make_k", "stop"
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6
HOST_WAIT_MS = int(tolerances.JOIN_WAIT_S * 3 * 1000)

# The server's own stop clocks for these tests (seconds): a short grace and kill, as the in-library
# variants, and (deadline variant) a one second finalization reserve and a three second release
# slice (see `test_tr4_unit5_lib.SHORT_RELEASE_S`: one second passed idle, not on a loaded runner).
GRACE_S = support.TEST_GRACE_S
KILL_S = support.TEST_KILL_S
SHORT_RELEASE_S = 3.0
SHORT_RESERVE_S = 1.0
SHORT_MARGIN_S = (
    12.0  # admission's finalization margin: the tree's release phase needs 10 s after the deadline
)
SHORT_DEADLINE_S = 10
DEFAULT_RELEASE_S = (
    10.0  # `clock.release_slice`'s default: the server's, unless the test shortens it
)

# The mode each variant asks `creator_pk` for, and what the root's answer says of it.
MODE = {
    "sibling_fail": "sibling_fail",
    "exception": "exception",
    "cancel": "hold",
    "deadline": "deadline",
    "restart": "hold",
}
OUTCOME = {
    "sibling_fail": ("failed", None),
    "exception": ("execution_error", None),
    "cancel": ("cancelled", "cancel"),
    "deadline": ("timed_out", "release_point"),
}
# The in-library test's deadline variant shortens these constants of the fixture (once each).
SHORT = [
    ("WAIT_MAX_S = 6", "WAIT_MAX_S = 1"),
    ("RELEASE_TIMEOUT_S = 2", "RELEASE_TIMEOUT_S = 1"),
    ("LEAF_BUDGET_S = 10", "LEAF_BUDGET_S = 3"),
    ("ROOT_BUDGET_S = 20", "ROOT_BUDGET_S = 5"),
    ("DEADLINE_S = 36", "DEADLINE_S = 10"),
    ("deadline=36", "deadline=10"),
]


# the lift set's clause (J-TRL T1 reads it from the result's `labels`, MC-B3-04)
PROVES_A84 = pytest.mark.proves("WR-UNIT-5", "A8.4", "A", "tree", "PROC", "CI")


def _proves(variant: str) -> Any:
    marks = [
        pytest.mark.proves(
            "WR-UNIT-5",
            f"WR-UNIT-5:p-dead-k-released-{variant.replace('_', '-')}",
            "A",
            "tree",
            "PROC",
            "CI",
        ),
        pytest.mark.proves("WR-OWN-3", "WR-OWN-3:tree-process", "A", "tree", "PROC", "CI"),
        PROVES_A84,
    ]
    if variant == "restart":
        marks.append(
            pytest.mark.proves(
                "WR-UNIT-5", "WR-UNIT-5:recovery-ends-p-and-k", "A", "tree", "PROC", "CI"
            )
        )
    else:
        for label in ("sibling-not-found", "release-reverse-dependency-order"):
            marks.append(
                pytest.mark.proves("WR-UNIT-5", f"WR-UNIT-5:{label}", "A", "tree", "PROC", "CI")
            )
    return pytest.param(variant, marks=marks, id=variant)


VARIANTS = [_proves(v) for v in ("sibling_fail", "exception", "cancel", "deadline", "restart")]


# -- the host ------------------------------------------------------------------------------


def _source(*, short: bool) -> str:
    source = tk.fixture_source("creator_pk")
    if short:
        for old, new in SHORT:
            assert source.count(old) == 1, f"creator_pk: {old!r} moved"
            source = source.replace(old, new)
    return source


def _server_env(monkeypatch: pytest.MonkeyPatch, *, short: bool) -> None:
    """The `trestle serve` subprocess inherits this process's environment (`McpHost`), and its run
    wrapper and child inherit the server's: short stop clocks for every variant."""
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", str(GRACE_S))
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", str(KILL_S))
    if short:
        monkeypatch.setenv("TRESTLE_RELEASE_SLICE_S", str(SHORT_RELEASE_S))
        monkeypatch.setenv("TRESTLE_FINALIZATION_RESERVE_S", str(int(SHORT_RESERVE_S)))
        monkeypatch.setenv("TRESTLE_FINALIZATION_MARGIN_S", str(SHORT_MARGIN_S))


def _stop_bound(*, short: bool) -> float:
    """The server's bound on a stop (WR-CANCEL-1, MC-09), from the clocks it was started with."""
    return (SHORT_RELEASE_S if short else DEFAULT_RELEASE_S) + GRACE_S + KILL_S


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[mcp_host.McpHost]:
    """A `trestle serve` subprocess on a fresh home, under the short stop clocks."""
    _server_env(monkeypatch, short=False)
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as server:
        yield server


def _host_with_short_clocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mcp_host.McpHost:
    _server_env(monkeypatch, short=True)
    return mcp_host.McpHost(home=tmp_path / "host-short-home", timeout_s=HOST_TIMEOUT_S)


def _publish(server: mcp_host.McpHost, *, short: bool = False) -> None:
    (server.home / "plugins" / "creator_pk.py").write_text(_source(short=short), encoding="utf-8")


def _start(server: mcp_host.McpHost, mode: str, *, terminal: bool) -> int:
    """The MCP `run` tool on the server, sent without waiting for its answer: the run's own state
    is read from outside while the call is held. `wait_ms=0` answers at once with the run id."""
    args: dict[str, Any] = {
        "plugin": "creator_pk",
        "args": {"env": "dev", "mode": mode},
        "wait_ms": hostpath.WAIT_MS * 3 if terminal else 0,
    }
    if terminal:
        args["completion"] = "terminal"
    return server.hold("run", args)


def _await_view(server: mcp_host.McpHost, run_id: str) -> dict[str, Any]:
    answer = server.call(
        "await_runs", {"run_ids": [run_id], "mode": "all", "timeout_ms": tolerances.HARNESS_WAIT_MS}
    )
    views = answer["result"] if isinstance(answer, dict) and "result" in answer else answer
    (view,) = views
    assert isinstance(view, dict), view
    return view


def _run_dir(server: mcp_host.McpHost) -> Path | None:
    """The one run the host has admitted (each test has a fresh home), once it exists."""
    found = sorted(p for p in (server.home / "runs").glob("*/r_*") if p.is_dir())
    return found[0] if found else None


# -- what the run left, read from outside --------------------------------------------------


def _markers(run_dir: Path) -> Path:
    return run_dir / "work" / "tmp" / "markers"


def _selectors(run_id: str) -> dict[str, str]:
    """`P`'s and `K`'s run-scoped selectors: what the fake derives from `(root, node, effect)`."""
    lineage = SimpleNamespace(root_run_id=run_id, path=SimpleNamespace(segments=("c",)))
    return {
        "P": run_scoped_selector(lineage, P_EFFECT),
        "K": run_scoped_selector(lineage, K_EFFECT),
    }


def _live_processes(run_dir: Path) -> dict[str, ancestry.ProcInfo]:
    """`P` and `K` as the process table shows them, once both exist (empty until then): the fake's
    marker files hold each one's pid."""
    found: dict[str, int] = {}
    for label, selector in _selectors(run_dir.name).items():
        try:
            record = json.loads(
                (_markers(run_dir) / f"{selector}.marker").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        if record.get("kind") != "process":
            return {}
        found[label] = int(record["pid"])
    table = {p.pid: p for p in ancestry.snapshot()}
    if not all(pid in table for pid in found.values()):
        return {}
    return {label: table[pid] for label, pid in found.items()}


def _both_up(server: mcp_host.McpHost, live: dict[str, ancestry.ProcInfo]) -> bool:
    run_dir = _run_dir(server)
    if run_dir is not None:
        live.update(_live_processes(run_dir))
    return len(live) == 2


def _inventory(run_dir: Path) -> frozenset[str]:
    return frozenset(FakeMarker(_markers(run_dir), "run").inventory()["containers"])


def _gone(before: dict[str, ancestry.ProcInfo]) -> bool:
    return not ancestry.survivors(set(before.values()), ancestry.snapshot())


def _rows(run_dir: Path) -> list[dict[str, Any]]:
    return list(records.ledger_rows(run_dir).rows)


def _terminal(rows: list[dict[str, Any]]) -> int:
    return next(n for n, row in enumerate(rows) if row["kind"] in TERMINAL_KINDS)


def _lane(run_dir: Path) -> records.LaneRows:
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return lane


def _release_rows(run_dir: Path) -> dict[str, int]:
    """The ledger index of the row that carries each created resource's release: the folded lane's
    `released` entry for it. `lane_folded` rows carry no effect name: it is read from the lane."""
    effects = {row.entry["seq"]: row.entry.get("effect", "") for row in _lane(run_dir).rows}
    found: dict[str, int] = {}
    for n, row in enumerate(_rows(run_dir)):
        if row["kind"] == "lane_folded" and row["entry_class"] == "released":
            found[effects[row["lane_seq"]]] = n
    return found


def _sibling_looks(run_dir: Path) -> list[dict[str, Any]]:
    """`sib`'s `step.observed` facts, from the run's evidence."""
    events = (evidence_dir(run_dir) / "events.ndjson").read_text(encoding="utf-8").splitlines()
    return [
        e["payload"]
        for e in map(json.loads, events)
        if e["kind"] == "step.observed" and e["payload"]["path"] == "sib"
    ]


def _assert_claimed_before_made(run_dir: Path) -> None:
    """Each create was claimed before it was made (WR-UNIT-5:claim-before-effect): on the host's
    finalized lane, the issue carrying its MC-25 descriptor precedes its confirmation, in the record
    of the root run."""
    lane = _lane(run_dir)
    for effect in (P_EFFECT, K_EFFECT):
        mine = [r for r in lane.rows if r.path == "c" and r.entry.get("effect") == effect]
        assert [r.cls for r in mine[:2]] == ["issue", "confirmation"], (effect, mine)
        issue, confirmation = mine[0].entry, mine[1].entry
        assert issue["seq"] < confirmation["seq"]
        assert issue["facet"] == "create" and issue["release"]["form"] == "in_run_group", issue
        assert confirmation["status"] == "applied"


def _sibling_saw_neither(run_dir: Path, *, both: bool) -> None:
    looks = _sibling_looks(run_dir)
    assert looks and all(look["found"] == [] for look in looks), looks
    if both:  # named as created by this run, never as found (WR-UNIT-5)
        selectors = set(_selectors(run_dir.name).values())
        assert any(set(look.get("created_by_run", ())) == selectors for look in looks)


# -- the four live paths -------------------------------------------------------------------


def _live_variant(
    variant: str, host: mcp_host.McpHost
) -> tuple[Path, dict[str, ancestry.ProcInfo], dict[str, Any], float, float]:
    """Run `creator_pk` on the host to its terminal answer the way `variant` ends it. Returns the
    run dir, `P` and `K` as seen live, the wire answer, the seconds from both existing to the answer
    and from the request to it."""
    short = variant == "deadline"
    _publish(host, short=short)
    live: dict[str, ancestry.ProcInfo] = {}
    sent = time.monotonic()
    request = _start(host, MODE[variant], terminal=True)
    try:
        assert support.wait_until(lambda: _both_up(host, live), tolerances.JOIN_WAIT_S), (
            "P and K never both existed"
        )
        run_dir = _run_dir(host)
        assert run_dir is not None
        up = time.monotonic()
        before = _inventory(run_dir)
        assert len(before) == 2, before  # both are live in the fake's inventory too
        if variant == "cancel":
            outcome = host.call("cancel", {"run_id": run_dir.name})
            assert outcome["code"] == codes.CANCEL_ACCEPTED, outcome
        answer = host.join(request, timeout=_stop_bound(short=short) + tolerances.JOIN_WAIT_S)
        done = time.monotonic()
    finally:
        ancestry.reap(set(live.values()))  # a failed run must not leave P or K behind
    assert answer["state"] in ("succeeded", "failed", "cancelled", "timed_out"), answer
    return run_dir, live, answer, done - up, done - sent


def _assert_live_variant(
    variant: str,
    run_dir: Path,
    live: dict[str, ancestry.ProcInfo],
    view: dict[str, Any],
    took: float,
    since_request: float,
) -> None:
    short = variant == "deadline"
    outcome, root_stop = OUTCOME[variant]
    answer = view["answer"]
    assert (answer["outcome"], answer["root_stop"]) == (outcome, root_stop)
    # P is gone, inside WR-CANCEL-1's bound of the moment the stop began (MC-13 snapshots, MC-09)
    # (a deadline's stop begins at its release point, seconds after both exist: its bound counts
    # from the admission, and the answer's own bound on the deadline is checked below)
    bound = _stop_bound(short=short) + clock.poll_interval + tolerances.SETTLE_S
    assert took <= bound + (SHORT_DEADLINE_S if short else 0), took
    assert _gone(live), "P or K is still in the process table at the terminal answer"
    if short:  # answered within the finalization margin of the admitted deadline
        assert since_request <= SHORT_DEADLINE_S + SHORT_MARGIN_S, since_request
    # the fake's inventory taken at the terminal answer agrees: nothing c created is left
    assert _inventory(run_dir) == frozenset()
    assert view["cleanup"]["processes"] == "released"
    assert answer["cleanup"]["clean"] is True and answer["cleanup"]["unknown"] == 0
    # K's release is on the record, before the terminal row; both, in reverse dependency order
    rows = _rows(run_dir)
    releases = _release_rows(run_dir)
    assert set(releases) == {K_EFFECT, P_EFFECT}, releases
    assert max(releases.values()) < _terminal(rows)
    assert releases[K_EFFECT] < releases[P_EFFECT]  # one node: reverse issue order, K first
    _assert_claimed_before_made(run_dir)
    _sibling_saw_neither(run_dir, both=True)


def _restart(host: mcp_host.McpHost) -> None:
    """The real server is SIGKILLed with the run live. Recovery, on the restarted server, ends `P`
    and `K` (by their recorded identities: both are in the run's group), each before the terminal
    row, and the answer is the recovered one."""
    _publish(host)
    live: dict[str, ancestry.ProcInfo] = {}
    started = _start(host, "hold", terminal=False)
    run_id = host.join(started)["run_id"]
    try:
        assert support.wait_until(lambda: _both_up(host, live), tolerances.JOIN_WAIT_S), (
            "P and K never both existed"
        )
        run_dir = _run_dir(host)
        assert run_dir is not None and run_dir.name == run_id
        assert len(_inventory(run_dir)) == 2
        assert support.wait_until(
            lambda: len(support.rows_of(run_dir, "process_identity")) >= 4,
            tolerances.JOIN_WAIT_S,
        )
        host.kill_server()
        assert not _gone(live), "the run's processes died with the server: nothing to recover"
        killed = time.monotonic()
        host.restart()  # recovery runs on start
        view = _await_view(host, run_id)
        took = time.monotonic() - killed
    finally:
        ancestry.reap(set(live.values()))
        ancestry.reap(support.marked(run_id))

    answer = view["answer"]
    assert view["state"] == "interrupted"
    assert (answer["outcome"], answer["root_stop"], answer["recovered"]) == (
        "execution_error",
        "restart",
        True,
    )
    assert view["cleanup"] == {"processes": "released"}
    # P and K are gone, inside the bound of the restart (MC-13 snapshot after, and the fake's own)
    assert (
        took <= _stop_bound(short=False) + clock.poll_interval + tolerances.HARNESS_WAIT_MS / 1000
    )
    assert _gone(live), "P or K survived recovery"
    assert _inventory(run_dir) == frozenset()
    # recovery ended them (the group, by identity), then finalized the run: terminal row last
    rows = _rows(run_dir)
    kinds = [row["kind"] for row in rows]
    (stop,) = [r for r in rows if r["kind"] == "group_stop"]
    assert (stop["confirmed_gone"], stop["method"]) == (True, "recovery")
    order = ["group_stop", "lane_folded", "error_record", "evidence_finalized"]
    positions = [kinds.index(kind) for kind in order]
    assert positions == sorted(positions) and positions[-1] < _terminal(rows)
    assert kinds[-1] == "interrupted" and kinds.count("interrupted") == 1
    # K's release is the group's: in_run_group, claimed before it was made, nothing else recorded
    _assert_claimed_before_made(run_dir)
    assert not [r for r in rows if r["kind"] == "sweep_disposition"]
    _sibling_saw_neither(run_dir, both=False)


@pytest.mark.parametrize("variant", VARIANTS)
def test_unit5_host_variant(variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`P` is gone within the stop bound, `K`'s release record precedes the terminal row, and the
    process table and the fake's inventory taken at the terminal answer show both absent, for a
    failing sibling, `c`'s own exception, a root cancel, the root deadline and a server killed and
    restarted, each through the MCP `run` tool of a real `trestle serve`."""
    if variant == "deadline":
        server = _host_with_short_clocks(tmp_path, monkeypatch)
    else:
        _server_env(monkeypatch, short=False)
        server = mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S)
    with server:
        if variant == "restart":
            _restart(server)
            return
        run_dir, live, view, took, since_request = _live_variant(variant, server)
        _assert_live_variant(variant, run_dir, live, view, took, since_request)


@PROVES_A84
@pytest.mark.proves("WR-UNIT-5", "WR-UNIT-5:claim-before-effect", "A", "tree", "PROC", "CI")
@pytest.mark.proves("WR-OWN-3", "WR-OWN-3:tree-process", "A", "tree", "PROC", "CI")
def test_host_claim_before_effect(host: mcp_host.McpHost) -> None:
    """On the finalized host record of `creator_pk`, every create effect record is preceded by its
    claim, and the claim carries an MC-25 descriptor in the root record."""
    _publish(host)
    answer = hostpath.mcp_run_tree(host, "creator_pk", {"env": "dev", "mode": "sibling_fail"})
    assert "code" not in answer and answer["answer"]["outcome"] == "failed", answer
    run_dir = _run_dir(host)
    assert run_dir is not None and run_dir.name == answer["run_id"]
    _assert_claimed_before_made(run_dir)
    # each claim's descriptor is what the ledger folded for it
    folded = [
        (r["lane_seq"], r["descriptor"])
        for r in _rows(run_dir)
        if r["kind"] == "lane_folded" and r["entry_class"] == "issue" and r["path"] == "c"
    ]
    lane = _lane(run_dir)
    issues = {
        r.entry["seq"]: r.entry
        for r in lane.rows
        if r.cls == "issue" and r.entry["effect"] in (P_EFFECT, K_EFFECT)
    }
    assert issues and set(issues) <= {seq for seq, _ in folded}
    for seq, descriptor in folded:
        assert descriptor == {"form": "in_run_group", "helpers_disclosed": False}, (seq, descriptor)


@PROVES_A84
@pytest.mark.proves(
    "WR-UNIT-5", "WR-UNIT-5:no-release-before-root-phase", "A", "tree", "PROC", "CI"
)
@pytest.mark.proves("WR-OWN-3", "WR-OWN-3:tree-process", "A", "tree", "PROC", "CI")
def test_host_no_release_before_root_phase(host: mcp_host.McpHost) -> None:
    """`c` reaches its own end long before the root does; nothing of it is released until the
    root's release phase: no release record precedes the root's own end record on the host's lane,
    and every release record follows every node's end."""
    _publish(host)
    answer = hostpath.mcp_run_tree(host, "creator_pk", {"env": "dev", "mode": "sibling_fail"})
    assert "code" not in answer and answer["answer"]["outcome"] == "failed", answer
    run_dir = _run_dir(host)
    assert run_dir is not None
    lane = _lane(run_dir)
    (root_end,) = [r for r in lane.rows if r.cls == "end" and r.path == ""]
    node_ends = {r.path: r.entry["seq"] for r in lane.rows if r.cls == "end"}
    assert node_ends["c"] < root_end.entry["seq"]  # c was done, and still holds P and K
    releases = [
        r
        for r in lane.rows
        if r.cls == "released"
        or (r.cls in ("issue", "confirmation") and r.entry.get("effect") == STOP_EFFECT)
    ]
    assert {r.entry["effect"] for r in releases if r.cls == "released"} == {P_EFFECT, K_EFFECT}
    assert all(r.entry["seq"] > root_end.entry["seq"] for r in releases), releases
    assert all(r.entry["seq"] > max(node_ends.values()) for r in releases)
