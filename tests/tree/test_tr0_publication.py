"""L.TR-0.4: a tree the declaration alone shows to be unpublishable is refused at publication, and
root-entry eligibility (OQ-31) is a parameter with both variants written.

V-11 places the whole-tree check that needs no request at publication: no snapshot is promoted, so
no run id exists. The refusals (`publication.unit_unresolved`, `publication.dependency_cycle`,
`publication.declaration_conflict`, and `publication.plan_precondition_uncovered` for OQ-31's
`refuse_at_publication` variant) come through `ControlSurface.publish_plugin` and the MCP
`publish_plugin` tool (MC-12) alike, and a refused publication keeps the previous snapshot."""

from __future__ import annotations

import typing
from pathlib import Path

import pytest

from tests.proof import mcp_host, meta, records, tolerances
from trestle.common import clock
from trestle.common.plan import declared as declared_format
from trestle.common.types import PublishView, RequestOutcome

REPO = Path(__file__).resolve().parents[2]
TREES = REPO / "tests" / "fixtures" / "trees"

UNRESOLVED = "publication.unit_unresolved"
CYCLE = "publication.dependency_cycle"
CONFLICT = "publication.declaration_conflict"
UNCOVERED_AT_PUBLICATION = "publication.plan_precondition_uncovered"
UNCOVERED_IN_NODE = "execution.plan_precondition_uncovered"
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 120.0

proves_unknown = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:unknown-descendant-publication", "A", "tree", "LOGIC+MCP", "CI"
)
proves_cycle = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:cycle-refused-at-publication", "A", "tree", "LOGIC+MCP", "CI"
)
proves_conflict = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:literal-conflict-refused-at-publication", "A", "tree", "LOGIC+MCP", "CI"
)
proves_oq31 = pytest.mark.proves(
    "WR-UNIT-1", "WR-UNIT-1:eligibility-oq31", "A", "tree", "LOGIC+MCP", "CI"
)


def _source(name: str) -> str:
    return (TREES / f"{name}.py").read_text(encoding="utf-8")


def _edited(name: str, old: str, new: str) -> str:
    source = _source(name)
    assert source.count(old) == 1, f"{name}: {old!r} moved"
    return source.replace(old, new)


def _snapshots(home: Path) -> set[str]:
    return {p.name for p in (home / "snapshots").glob("snap_*")}


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    from tests.proof import harness

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    return harness.fresh_kernel([plugins], home=tmp_path / "home")


def _refused(result: object, code: str, *naming: str) -> None:
    assert isinstance(result, RequestOutcome), result
    assert result.code == code, result
    assert result.origin == "publication" and result.retryable is False
    for name in naming:
        assert name in result.message, (name, result.message)


_GHOST = 'group("haunted", (ChildBinding(unit="ghost", params={}, needs=()),))'
UNKNOWN_FIXED = (
    f'units={{"haunted": {_GHOST}}},',
    f'units={{"haunted": {_GHOST}, "ghost": leaf("ghost")}},',
)


@proves_unknown
def test_unresolvable_descendant_refused_names_unit(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    fixed = _edited("unknown_descendant", *UNKNOWN_FIXED)
    published = kernel.control.publish_plugin(fixed)
    assert isinstance(published, PublishView), published
    before = _snapshots(kernel.home)

    # through ControlSurface: the refusal names the unit, no new snapshot, the previous one serves
    refused = kernel.control.publish_plugin(_source("unknown_descendant"))
    _refused(refused, UNRESOLVED, "ghost")
    assert _snapshots(kernel.home) == before
    served = kernel.registry.get("unknown_descendant")
    assert served is not None and served.snapshot_id == published.snapshot_id

    # through the MCP publish_plugin tool (MC-12): the same code and the same absence of a snapshot
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        first = host.call("publish_plugin", {"source": fixed})
        assert first.get("code") is None, first
        seen = _snapshots(host.home)
        answer = host.call("publish_plugin", {"source": _source("unknown_descendant")})
        assert answer["code"] == UNRESOLVED and "ghost" in answer["message"], answer
        assert _snapshots(host.home) == seen


@proves_cycle
def test_cycle_refused_at_publication_no_snapshot(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    fixed = _edited(
        "cycle",
        'ChildBinding(unit="a", params={}, needs=("b",))',
        'ChildBinding(unit="a", params={}, needs=())',
    )
    published = kernel.control.publish_plugin(fixed)
    assert isinstance(published, PublishView), published
    before = _snapshots(kernel.home)
    refused = kernel.control.publish_plugin(_source("cycle"))
    assert isinstance(refused, RequestOutcome) and refused.code == CYCLE, refused
    assert "a" in refused.message or "b" in refused.message  # a node on the cycle
    assert _snapshots(kernel.home) == before  # no snapshot dir
    served = kernel.registry.get("cycle")
    assert served is not None and served.snapshot_id == published.snapshot_id
    # a containment cycle (a composite that contains itself) is the same refusal
    contained = fixed.replace(
        '"a": leaf("a"),', '"a": group("a", (ChildBinding(unit="a", params={}, needs=()),)),'
    )
    assert contained != fixed
    _refused(kernel.control.publish_plugin(contained), CYCLE)


DANGLING = {
    "needs": (
        "two_branch_barrier",
        'needs=("left", "right")',
        'needs=("left", "nope")',
        "nope",
    ),
    "gates": (
        "identifier_sets",
        "    env_key_field=None,\n)",
        '    env_key_field=None,\n    gates=("nope",),\n)',
        "nope",
    ),
    "fallback": ("choice_fake", 'fallback="fake_a"', 'fallback="nope"', "nope"),
}


@proves_unknown
@pytest.mark.parametrize("kind", sorted(DANGLING))
def test_dangling_needs_gates_fallback_refused_at_publication(kind: str, tmp_path: Path) -> None:
    name, old, new, entry = DANGLING[kind]
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(_source(name)), PublishView)  # the valid form
    refused = kernel.control.publish_plugin(_edited(name, old, new))
    _refused(refused, UNRESOLVED, entry)  # naming the entry
    (snap,) = [s for s in [kernel.registry.get(name)] if s is not None]
    assert snap is not None


@proves_conflict
def test_literal_param_conflict_refused_at_publication(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    refused = kernel.control.publish_plugin(_source("conflict"))  # size 1 vs size 2
    _refused(refused, CONFLICT, "worker")
    assert not (kernel.home / "snapshots").exists() or not _snapshots(kernel.home)
    # equal literals are one node, not a conflict
    same = _edited("conflict", '"size": 2', '"size": 1')
    assert isinstance(kernel.control.publish_plugin(same), PublishView)
    # a difference where a value may be a reference to a request argument is admission's (V-1.2)
    deferred = _source("conflict").replace('{"size": 1}', '{"size": "args.size"}')
    assert isinstance(kernel.control.publish_plugin(deferred), PublishView)
    # the shared node of `shared_diamond` binds one literal set twice and publishes
    assert isinstance(kernel.control.publish_plugin(_source("shared_diamond")), PublishView)


def test_root_entry_eligibility_parameter_is_declared() -> None:
    assert typing.get_args(declared_format.RootEligibility) == (
        "refuse_at_publication",
        "admit_and_stop",
    )
    assert declared_format.ROOT_ELIGIBILITY == "admit_and_stop"  # the shipped variant


RUNNABLE_OBSERVE = (
    "    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:\n"
    "        raise NotImplementedError(STRUCTURAL)",
    "    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:\n"
    "        return Observation(\n"
    "            present=False,\n"
    "            selector_present=False,\n"
    "            identity_proven=False,\n"
    "            configuration_compatible=True,\n"
    "            postcondition=CheckResult(False, None, ''),\n"
    "            preconditions=(),\n            currency=(),\n            found=(),\n"
    "            code=None,\n            payload=None,\n        )",
)


def _runnable_sibling_only_leaf() -> str:
    """`sibling_only_leaf` with the one behaviour the loop needs to reach its in-node check: an
    `observe` that carries no check for the declared precondition, and a callable that runs the
    tree. Nothing else differs from the fixture."""
    source = _edited("sibling_only_leaf", *RUNNABLE_OBSERVE)
    source = source.replace(
        "from trestle.workflow import (",
        "from trestle.workflow import (\n    CheckResult,\n    Observation,",
    )
    source = source.replace(
        "from trestle.plugin import Context, trestle\n",
        "from trestle.plugin import Context, trestle\nfrom trestle.workflow.loop import run_tree\n",
    )
    old = '    return {"fixture": "sibling_only_leaf"}'
    assert old in source
    return source.replace(old, "    run_tree(ctx, ENTRY, {}, ports={})\n" + old)


@proves_oq31
@pytest.mark.parametrize("variant", ["refuse_at_publication", "admit_and_stop"])
def test_root_entry_eligibility(
    variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(declared_format, "ROOT_ELIGIBILITY", variant)
    kernel = _kernel(tmp_path)

    # a tree whose units declare nothing another node must cover publishes under both variants
    assert isinstance(kernel.control.publish_plugin(_source("root_eligible_both")), PublishView)

    sibling_only = _source("sibling_only_leaf")
    if variant == "refuse_at_publication":
        refused = kernel.control.publish_plugin(sibling_only)
        _refused(refused, UNCOVERED_AT_PUBLICATION, "consumer", "producer_ready")
        assert (
            not any((kernel.home / "runs").glob("*/*")) if (kernel.home / "runs").exists() else True
        )
        return

    # admit_and_stop adds no publication refusal: it publishes, and the existing in-node refusal
    # stops it before any effect (one terminal call, the stable code, zero claim entries)
    assert isinstance(kernel.control.publish_plugin(sibling_only), PublishView)
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        source = _runnable_sibling_only_leaf()
        published = host.call("publish_plugin", {"source": source})
        assert published.get("code") is None, published
        answer = host.call(
            "run",
            {
                "plugin": "sibling_only_leaf",
                "args": {},
                "wait_ms": int(tolerances.HARNESS_WAIT_MS),
                "completion": "terminal",
            },
        )
        verdict = answer["answer"]
        assert verdict["outcome"] == "failed", answer
        assert verdict["primary"]["code"] == UNCOVERED_IN_NODE, answer
        (run_dir,) = sorted((host.home / "runs").glob("*/r_*"))
        lane = records.lane_rows(run_dir)
        assert not lane.problems and not lane.torn, lane.problems
        classes = [row.cls for row in lane.rows]
        assert "issue" not in classes and "confirmation" not in classes, classes


def test_eligibility_label_is_both_variant_and_listed() -> None:
    (label,) = [
        lb
        for lb in meta._load_all_labels()
        if lb["id"] == "WR-UNIT-1:eligibility-oq31"  # noqa: SLF001
    ]
    assert label["posture"] == "both_variant" and label["oq"] == "OQ-31"
    assert meta.load_open_questions()  # OQ-31 is listed, and nothing claims to decide it
    import argparse

    assert meta.cmd_open_questions(argparse.Namespace()) == 0
