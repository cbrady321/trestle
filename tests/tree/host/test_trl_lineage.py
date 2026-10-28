"""L.TR-L.4: host re-run of WR-UNIT-2 lineage and WR-UNIT-8 start records (J-TRL; MC-B3-02).

Every case reaches admission only through the two host entry points (`tests/tree/hostpath.py`): the
MCP `run` tool on a `trestle serve` subprocess (MC-12) and `ControlSurface.run`. Nothing here
imports `tests/proof/harness.py` (MC-26 writes an admitted run directly and bypasses admission), so
each case proves the property as a caller sees it, on the finalized record.

The first five cases are the lift set's A2.6 nodes (`lift_set_trl.toml`): lineage is assigned at
admission and never forgeable. A2.6 and its labels stay held by TM-B2-1's `choice-only` phase until
L.TR-5.3 removes the refusal; the sixth case is `WR-PLAN-5:tree` at its row's tier."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from tests.tree import hostpath
from tests.tree.test_tr1_admission import edited, publish, run_dirs, source
from trestle.common import codes
from trestle.common.types import RunView
from trestle.server import answer
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[3]
TREES = REPO / "tests" / "fixtures" / "trees"
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
ROOT_PATH = ""

TIER = "MCP+LOGIC"
proves_a26 = pytest.mark.proves("WR-UNIT-2", "A2.6", "A", "tree", TIER, "CI")
proves_forge = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:forge-field-ignored", "A", "tree", TIER, "CI"
)
proves_lineage = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:runtime-cannot-change-lineage", "A", "tree", TIER, "CI"
)
proves_resolves = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:child-view-resolves", "A", "tree", TIER, "CI"
)
proves_one_start = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:one-logical-node-one-start", "A", "tree", TIER, "CI"
)
proves_starts = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:start-records-within-declaration", "A", "tree", TIER, "CI"
)
proves_plan5 = pytest.mark.proves("WR-PLAN-5", "WR-PLAN-5:tree", "A", "tree", "PROC", "CI")

# The plugin the lineage cases run: a diamond (`left` and `right` both run the one node `shared`,
# as `shared_diamond` does) with real behaviour, so a run writes the lane records the cases read.
# `shared` is hostile: from `observe` and `advance` it tries to change the lineage it is handed
# (an ordinary assignment on the frozen dataclass), observes under a forged lineage and returns a
# payload naming a forged root and path. The request arguments named `root`, `path`,
# `root_run_id` and `lineage` are ordinary data.
DIAMOND_PLUGIN = '''\
"""A shared-node diamond with a unit that tries to rewrite its own lineage."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from trestle_packs.fakes import FakeMarker

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
from trestle.workflow.units import ActContext, Acted, EffectFacets, ObserveContext, ReadFacets, Step
from trestle.workflow.values import (
    CheckResult,
    CreatedHandle,
    FoundRef,
    NodePath,
    Observation,
    Verdict,
)

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
TAMPER_LOG: list[Path] = []
FORGED_PATH = ("x", "y")


def tamper(ctx: Any, where: str) -> str:
    """Try to move the lineage the host handed this unit (an ordinary assignment on the frozen
    dataclass) and to act under another one; log what the attempt did."""
    try:
        ctx.lineage.path = NodePath(FORGED_PATH)
        outcome = "assign:changed"
    except AttributeError:
        outcome = "assign:refused"
    if TAMPER_LOG:
        with TAMPER_LOG[0].open("a", encoding="utf-8") as log:
            log.write(where + " " + outcome + "\\n")
    return outcome


def forged_lineage(ctx: Any) -> Any:
    """The handed lineage with a forged path: the unit's attempt to observe as another node."""
    return replace(ctx.lineage, path=NodePath(FORGED_PATH))


class Node:
    def __init__(self, unit: str, *, hostile: bool) -> None:
        self._unit = unit
        self._hostile = hostile
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "lineage-diamond", None
        )

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=6)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(
                EffectDeclaration(
                    CREATE_EFFECT,
                    EffectFacetClass.CREATE,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=2),
                ),
                EffectDeclaration(
                    STOP_EFFECT,
                    EffectFacetClass.OWNED,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=2),
                    is_release=True,
                ),
            ),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=10),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        if self._hostile:
            tamper(ctx, "observe")
        resource = reads.read(ResourceReads)
        if self._hostile:
            resource.observe(self._spec, forged_lineage(ctx), CREATE_EFFECT)
        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)
        checked = resource.check("ready", seen.selector_ref) if seen.selector_ref else None
        ready = checked is not None and checked.satisfied
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(ready, None, ""),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload={"root_run_id": "r_forged", "path": "x/y"} if self._hostile else None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if self._hostile:
            tamper(ctx, "advance")
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def group(unit: str, children: tuple[ChildBinding, ...], budget: int, env: str | None):
    return AllDeclaration(
        unit=unit,
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=children,
        concurrency=2,
        budget=timedelta(seconds=budget),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=env,
    )


SHARED = ChildBinding(unit="shared", params={"mode": "common"}, needs=())

ENTRY = WorkflowEntry(
    root="diamond",
    units={
        "diamond": group(
            "diamond",
            (
                ChildBinding(unit="left", params={}, needs=()),
                ChildBinding(unit="right", params={}, needs=()),
            ),
            60,
            "env",
        ),
        "left": group("left", (SHARED,), 30, None),
        "right": group("right", (SHARED,), 30, None),
        "shared": Node("shared", hostile=True),
    },
    deadline=timedelta(seconds=90),
)


@trestle(deadline=90, env_arg="env")
def lineage_diamond(
    ctx: Context,
    env: str = "dev",
    root: str = "",
    path: str = "",
    root_run_id: str = "",
    lineage: str = "",
) -> dict[str, str]:
    TAMPER_LOG.append(ctx.tmp / "tamper.log")
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env, "root": root, "path": path, "root_run_id": root_run_id}
'''

FORGED_ARGS = {
    "env": "dev",
    "root": "evil",
    "path": "x/y",
    "root_run_id": "r_forged",
    "lineage": "l",
}
DIAMOND_PATHS = ("", "left", "left/shared", "right")  # `right/shared` is `left/shared`


@pytest.fixture
def mcp(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        yield host


def plant_source(host: mcp_host.McpHost, name: str, text: str) -> None:
    (host.home / "plugins" / f"{name}.py").write_text(text, encoding="utf-8")


def plant_fixture(host: mcp_host.McpHost, name: str) -> None:
    shutil.copy(TREES / f"{name}.py", host.home / "plugins")


def admitted_run(host: mcp_host.McpHost, plugin: str, args: dict[str, Any]) -> dict[str, Any]:
    """The MCP `run` tool over `plugin`: admitted (a run id, no refusal code) and terminal."""
    wired = hostpath.mcp_run_tree(host, plugin, args)
    assert "code" not in wired, wired
    assert str(wired["run_id"]).startswith("r_"), wired
    assert wired["state"] in TERMINAL_STATES, wired
    return wired


def child_views(host: mcp_host.McpHost, handles: list[str]) -> list[dict[str, Any]]:
    """The views the MCP `await_runs` tool returns for child handles (the wire wraps the list)."""
    wired = host.call("await_runs", {"run_ids": handles, "timeout_ms": 0})
    assert isinstance(wired, dict) and "code" not in wired, wired
    views = wired["result"]
    assert isinstance(views, list) and len(views) == len(handles), wired
    return views


def run_dir_of(host: mcp_host.McpHost, run_id: str) -> Path:
    (found,) = (host.home / "runs").glob(f"*/{run_id}")
    return found


def spec_of(run_dir: Path) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(
        (run_dir / "evidence" / "spec.json").read_text(encoding="utf-8")
    )
    return spec


def vertex_paths(run_dir: Path) -> list[str]:
    """The admitted plan's vertex paths as the run's `spec.json` records them (`""` is the root)."""
    return [v["path"] for v in spec_of(run_dir)["plan"]["vertices"]]


def lane_paths(run_dir: Path) -> list[str]:
    """The path of every lane entry after the plan entry, in record order."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn and not lane.unknown, (lane.problems, lane.unknown)
    return [row.path for row in lane.rows if row.path is not None]


def tamper_log(run_dir: Path) -> list[str]:
    (log,) = run_dir.rglob("tamper.log")
    return log.read_text(encoding="utf-8").splitlines()


@proves_a26
@proves_forge
def test_forge_request_fields_not_honoured(mcp: mcp_host.McpHost) -> None:
    """Request arguments named `root`, `path`, `root_run_id` and `lineage` reach the plugin as
    ordinary data: the run id is the host's own, the run lives under it, and every recorded path
    is one of the declaration's canonical ones, never the forged `x/y`."""
    plant_source(mcp, "lineage_diamond", DIAMOND_PLUGIN)
    wired = admitted_run(mcp, "lineage_diamond", FORGED_ARGS)
    run_id = wired["run_id"]
    assert run_id != FORGED_ARGS["root_run_id"]
    assert not list((mcp.home / "runs").glob("*/r_forged"))
    run_dir = run_dir_of(mcp, run_id)
    assert list(spec_of(run_dir)["args"]) == list(FORGED_ARGS)  # kept as the request's arguments
    assert vertex_paths(run_dir) == ["", "left", "left/shared", "right"]
    paths = lane_paths(run_dir)
    assert paths and set(paths) <= set(DIAMOND_PATHS), sorted(set(paths))
    # the forged values came back in the plugin's own result, and nowhere in the answer's nodes
    assert wired["summary"]["root_run_id"] == "r_forged" and wired["summary"]["path"] == "x/y"
    listed = {tuple(node["path"]) for node in wired["answer"]["listed"]}
    assert ("x", "y") not in listed and listed <= {("left",), ("left", "shared"), ("right",), ()}
    root_row = json.loads((run_dir / "evidence" / "ledger.ndjson").read_text().splitlines()[0])
    assert root_row["run_id"] == run_id


@proves_a26
@proves_lineage
def test_unit_code_cannot_change_recorded_path(mcp: mcp_host.McpHost) -> None:
    """The unit tries, on every call, to assign its lineage, observes under a forged one and
    returns a payload naming a forged root and path. The lane still records
    the node only under its canonical path and every view the run publishes names the real root."""
    plant_source(mcp, "lineage_diamond", DIAMOND_PLUGIN)
    wired = admitted_run(mcp, "lineage_diamond", {"env": "dev"})
    run_dir = run_dir_of(mcp, wired["run_id"])
    attempts = tamper_log(run_dir)
    assert attempts, "the hostile unit never ran"
    assert all(line.endswith(" assign:refused") for line in attempts), attempts
    paths = lane_paths(run_dir)
    assert "left/shared" in paths and "x/y" not in paths and "shared" not in paths
    assert set(paths) <= set(DIAMOND_PATHS), sorted(set(paths))
    assert vertex_paths(run_dir) == ["", "left", "left/shared", "right"]  # the plan is untouched
    (shared,) = child_views(mcp, [answer.child_handle(wired["run_id"], ("left", "shared"))])
    assert (shared["root_run_id"], shared["path"]) == (wired["run_id"], "left/shared")


@proves_a26
@proves_resolves
def test_every_child_view_resolves_names_root_path(mcp: mcp_host.McpHost) -> None:
    """After a host run of a tree (`three_level` and `shared_diamond`), every vertex below the root
    has a view addressed by its handle (derived from the root run id and the vertex's path) that
    names its root and its path and is terminal; a handle for a path that is not a vertex, or under
    another root, resolves to nothing."""
    for fixture in ("three_level", "shared_diamond"):
        plant_fixture(mcp, fixture)
        run_id = admitted_run(mcp, fixture, {})["run_id"]
        paths = [p for p in vertex_paths(run_dir_of(mcp, run_id)) if p]
        assert paths, fixture
        handles = {p: answer.child_handle(run_id, tuple(p.split("/"))) for p in paths}
        assert len(set(handles.values())) == len(paths)  # one handle per vertex
        by_handle = {v["run_id"]: v for v in child_views(mcp, list(handles.values()))}
        assert set(by_handle) == set(handles.values()), fixture
        for path, handle in handles.items():
            view = by_handle[handle]
            assert (view["root_run_id"], view["path"]) == (run_id, path), view
            assert view["state"] in TERMINAL_STATES, view
        # the same vertex under another root is another handle
        other = admitted_run(mcp, fixture, {})["run_id"]
        assert other != run_id
        assert answer.child_handle(other, tuple(paths[0].split("/"))) != handles[paths[0]]
        for stray in (
            answer.child_handle(run_id, ("nope",)),
            answer.child_handle("r_nosuchroot", tuple(paths[0].split("/"))),
        ):
            missing = mcp.call("await_runs", {"run_ids": [stray], "timeout_ms": 0})
            assert missing["result"]["code"] == codes.INVALID_HANDLE, missing


@proves_a26
@proves_one_start
def test_shared_node_one_start_one_disposition(mcp: mcp_host.McpHost) -> None:
    """`shared` is one logical node reached through `left` and `right`: the lane holds one vertex
    for it (`left/shared`), one attempt of each of its effects, one `NodeEnd`, and no record under
    any other path for it; the answer lists it once."""
    plant_source(mcp, "lineage_diamond", DIAMOND_PLUGIN)
    wired = admitted_run(mcp, "lineage_diamond", {"env": "dev"})
    run_dir = run_dir_of(mcp, wired["run_id"])
    lane = records.lane_rows(run_dir)
    shared = [row.entry for row in lane.rows if row.path == "left/shared"]
    issues = [e for e in shared if e["class"] == "issue"]
    assert [(e["effect"], e["attempt"]) for e in issues] == [("up", 1), ("stop", 1)], issues
    ends = [e for e in shared if e["class"] == "end"]
    assert len(ends) == 1 and ends[0]["condition"] == "satisfied", ends
    assert not [
        row
        for row in lane.rows
        if row.path is not None and row.path.endswith("/shared") and row.path != "left/shared"
    ]
    # the unit itself acted once: `advance` ran one time although two parents reach it
    assert [line.split(" ")[0] for line in tamper_log(run_dir)].count("advance") == 1
    listed = [tuple(node["path"]) for node in wired["answer"]["listed"]]
    assert listed.count(("left", "shared")) == 1 and ("right", "shared") not in listed, listed
    handles = [answer.child_handle(wired["run_id"], p) for p in (("left", "shared"), ("right",))]
    views = child_views(mcp, handles)
    disposition = {v["path"]: v["answer"]["disposition"] for v in views}
    assert disposition["left/shared"] == "started", disposition


@proves_a26
@proves_starts
def test_started_paths_within_declaration(mcp: mcp_host.McpHost) -> None:
    """Every start record a run wrote (each `issue` and `step` entry of its lane) is at a path of
    the admitted plan, each admitted path is a node of the published declaration, and a node cut
    before it started has no start record. Three trees: the diamond, an ordinary failure that cuts
    two dependents (`failure_dependents`) and an exception that stops its siblings
    (`exception_branch`)."""
    plant_source(mcp, "lineage_diamond", DIAMOND_PLUGIN)
    plant_fixture(mcp, "failure_dependents")
    plant_fixture(mcp, "exception_branch")
    for plugin, args in (
        ("lineage_diamond", {"env": "dev"}),
        ("failure_dependents", {"env": "dev", "mode": "failed"}),
        ("exception_branch", {"env": "dev"}),
    ):
        run_dir = run_dir_of(mcp, admitted_run(mcp, plugin, args)["run_id"])
        admitted = set(vertex_paths(run_dir))
        declaration = mcp.home / "snapshots" / spec_of(run_dir)["snapshot_id"] / "declaration.json"
        declared = set(json.loads(declaration.read_text(encoding="utf-8"))["nodes"])
        assert admitted <= declared, (plugin, sorted(admitted - declared))
        lane = records.lane_rows(run_dir)
        assert not lane.problems and not lane.torn and not lane.unknown, plugin
        started = {str(row.path) for row in lane.rows if row.entry["class"] in ("issue", "step")}
        assert started and started <= admitted, (plugin, sorted(started - admitted))
        assert set(lane_paths(run_dir)) <= admitted, plugin
        cut = {
            str(row.path)
            for row in lane.rows
            if row.entry["class"] == "end" and row.entry["cut"] == "not_started"
        }
        assert not cut & started, (plugin, sorted(cut & started))  # never both cut and started


@proves_plan5
def test_descendant_edit_moves_admitted_identity_proc(tree_kernel: Kernel) -> None:
    """`three_level` is published through the real validator subprocess and a root R1 admitted
    through `ControlSurface.run`; a descendant declaration is edited and republished; the next root
    R2 records a different identity in its `spec.json`, while R1's `spec.json` is unchanged and R1
    ended under it."""
    name = publish(tree_kernel, source("three_level"))
    first = hostpath.run_tree_via_host(tree_kernel, name)
    assert isinstance(first, RunView), first
    (r1_dir,) = run_dirs(tree_kernel)
    r1_bytes = (r1_dir / "evidence" / "spec.json").read_bytes()
    r1 = json.loads(r1_bytes)
    assert first.state in TERMINAL_STATES

    republished = publish(
        tree_kernel,
        edited("three_level", '"cache": leaf("cache"),', '"cache": leaf("cache", budget=50),'),
    )
    assert republished == name
    second = hostpath.run_tree_via_host(tree_kernel, name)
    assert isinstance(second, RunView), second
    assert second.run_id != first.run_id
    r2 = spec_of(next(d for d in run_dirs(tree_kernel) if d.name == second.run_id))
    assert r2["snapshot_id"] != r1["snapshot_id"]
    assert r2["manifest_sha256"] != r1["manifest_sha256"]
    assert r2["plan"]["declaration_digest"] != r1["plan"]["declaration_digest"]
    # the root admitted before the edit keeps its own identity and ended under it
    assert (r1_dir / "evidence" / "spec.json").read_bytes() == r1_bytes
    assert first.state in TERMINAL_STATES
    ended = tree_kernel.control.project.status(first.run_id)
    assert isinstance(ended, RunView) and ended.state in TERMINAL_STATES
    assert ended.run_id == first.run_id
    assert ended.outcome is not None
    assert ended.outcome["identity"]["snapshot_id"] == r1["snapshot_id"]
