"""L.TR-1.2: logical-node identity, conflict refusal, lineage with no public constructor.

V-1.2: every reference to one logical node under one root is one vertex; two references whose
bound parameters differ once request arguments are resolved are a declaration conflict, refused
before a run id exists (the literal half is publication's, L.TR-0.4); the same unit bound under
two names is two nodes. V-1.1: lineage `(root run id, canonical path)` is host-assigned, so no
request field, argument or declaration field can supply or override it, and plugin code is handed
no constructor for it."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from tests.single.control import support
from tests.tree.test_tr1_admission import KEY, publish, refused, run_dirs, source
from trestle.common import codes
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import AdmitRequest, AdmitResultAdmitted
from trestle.server.admission import plan_for_admission
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[2]
DEADLINE_S = 300.0

_LITERAL_1 = 'ChildBinding(unit="worker", params={"size": 1}, needs=())'
_LITERAL_2 = 'ChildBinding(unit="worker", params={"size": 2}, needs=())'
_SIGNATURE = "def conflict(ctx: Context) -> dict[str, str]:"


def _swap(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"{old!r} moved"
    return text.replace(old, new)


def by_argument() -> str:
    """`conflict` with both references' `size` naming a request argument instead of a literal:
    publishable (a string may be an argument reference), conflicting when the values differ."""
    text = source("conflict")
    text = _swap(
        text, _LITERAL_1, 'ChildBinding(unit="worker", params={"size": "size_a"}, needs=())'
    )
    text = _swap(
        text, _LITERAL_2, 'ChildBinding(unit="worker", params={"size": "size_b"}, needs=())'
    )
    return _swap(
        text,
        _SIGNATURE,
        "def conflict(ctx: Context, size_a: int = 1, size_b: int = 1) -> dict[str, str]:",
    )


def two_names() -> str:
    """`conflict` with the two references named apart: one unit, two logical nodes."""
    text = _swap(source("conflict"), _LITERAL_1, _LITERAL_1[:-1] + ', name="first")')
    return _swap(text, _LITERAL_2, _LITERAL_2[:-1] + ', name="second")')


def plan_of(kernel: Kernel, plugin: str, args: dict[str, Any]) -> AdmittedPlan | Any:
    snap = kernel.registry.get(plugin)
    assert snap is not None
    return plan_for_admission(snap, AdmitRequest(plugin=plugin, args=args), DEADLINE_S)


@pytest.mark.proves("WR-UNIT-2", "WR-UNIT-2:conflict-refused", "A", "tree", "MCP+LOGIC", "CI")
def test_conflicting_params_refused_before_run_id(tree_kernel: Kernel) -> None:
    name = publish(tree_kernel, by_argument())
    outcome = refused(tree_kernel, name, {"size_a": 1, "size_b": 2})
    assert outcome.code == codes.DECLARATION_CONFLICT == "admission.declaration_conflict"
    assert "worker" in outcome.message, outcome.message  # the node
    assert run_dirs(tree_kernel) == []


@pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:one-logical-node-one-vertex", "A", "tree", "MCP+LOGIC", "CI"
)
def test_same_node_one_vertex(tree_kernel: Kernel) -> None:
    """Equal resolved parameters: the two references are one node, one vertex, run once."""
    name = publish(tree_kernel, by_argument())
    plan = plan_of(tree_kernel, name, {"size_a": 3, "size_b": 3})
    assert isinstance(plan, AdmittedPlan), plan
    assert [v.path for v in plan.vertices] == ["", "worker"]
    assert plan.vertex("").children == ("worker",)


@pytest.mark.proves("WR-UNIT-2", "WR-UNIT-2:two-nodes-not-refused", "A", "tree", "MCP+LOGIC", "CI")
def test_same_unit_two_nodes_not_conflict(tree_kernel: Kernel) -> None:
    """One unit under two names is two nodes with two vertices, not a conflict: the tree compiles
    and gets the temporary code (until L.TR-L.1)."""
    name = publish(tree_kernel, two_names())
    plan = plan_of(tree_kernel, name, {})
    assert isinstance(plan, AdmittedPlan), plan
    assert [v.path for v in plan.vertices] == ["", "first", "second"]
    outcome = refused(tree_kernel, name)
    assert outcome.code == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED


FORGED = {"root": "evil", "path": "x/y", "root_run_id": "r_forged", "lineage": "l"}


def forging(text: str) -> str:
    """A plugin whose call arguments are named as lineage fields, and whose one child is bound
    with parameters named `root` and `path`."""
    text = _swap(
        text,
        _LITERAL_1,
        'ChildBinding(unit="worker", params={"root": "evil", "path": "x/y"}, needs=())',
    )
    text = _swap(text, _LITERAL_2, 'ChildBinding(unit="other", params={"lineage": "l"}, needs=())')
    text = _swap(
        text, '"worker": leaf("worker"),', '"worker": leaf("worker"), "other": leaf("other"),'
    )
    return _swap(
        text,
        _SIGNATURE,
        "def conflict(ctx: Context, root: str = '', path: str = '', root_run_id: str = '', "
        "lineage: str = '') -> dict[str, str]:",
    )


@pytest.mark.proves("WR-UNIT-2", "WR-UNIT-2:forge-field-ignored", "A", "tree", "MCP+LOGIC", "CI")
def test_forge_fields_not_honoured(tree_kernel: Kernel) -> None:
    """Request arguments and declaration parameters named `root`, `path`, `root_run_id` or
    `lineage` are ordinary data: the compiled paths are the declaration's canonical ones, and the
    run id an admission mints is the host's own."""
    name = publish(tree_kernel, forging(source("conflict")))
    clean = plan_of(tree_kernel, name, {})
    forged = plan_of(tree_kernel, name, dict(FORGED))
    assert isinstance(clean, AdmittedPlan) and isinstance(forged, AdmittedPlan)
    assert forged.paths == clean.paths == ("", "worker", "other")
    assert forged.root == clean.root and forged.selected_scope == clean.selected_scope
    assert set(forged.precedence_ordinal) == set(clean.paths)
    # a one-vertex root admitted with the forged arguments: the run id is minted, the plan's only
    # path is the root, and nothing of the request reaches the lineage the loop is handed
    plain = support.workflow_source("plain")
    plain = _swap(
        plain,
        'name: str = "x", other: str = "y"',
        "root: str = '', path: str = '', root_run_id: str = '', lineage: str = ''",
    )
    plain = _swap(plain, 'return {"name": name}', "return {}")
    publish(tree_kernel, plain)
    result = tree_kernel.control.admission.admit(
        AdmitRequest(plugin="plain", args=dict(FORGED), idempotency_key=KEY)
    )
    assert isinstance(result, AdmitResultAdmitted), result
    (run_dir,) = run_dirs(tree_kernel)
    assert result.run_id != FORGED["root_run_id"] and run_dir.name == result.run_id
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert [v["path"] for v in spec["plan"]["vertices"]] == [""]
    rows = [
        json.loads(line)
        for line in (run_dir / "evidence" / "ledger.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[0]["run_id"] == result.run_id


# Where a `Lineage` may be constructed under `trestle/`: the host's run services hand it out
# (`RunServices.lineage`, Boundary 2), and `lane_format` decodes the lane's own record type.
MINT_SITES = {"trestle/child/run_services.py", "trestle/common/lane_format.py"}
PLUGIN_FACING = ("trestle/workflow", "trestle/plugin")


def _python_files(*roots: str) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        base = REPO / root
        files += (
            [base.with_suffix(".py")]
            if base.with_suffix(".py").is_file()
            else sorted(base.rglob("*.py"))
        )
    return files


def _constructs_lineage(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name == "Lineage":
                return True
    return False


@pytest.mark.proves("WR-UNIT-2", "WR-UNIT-2:forge-field-ignored", "A", "tree", "MCP+LOGIC", "CI")
def test_no_public_lineage_constructor() -> None:
    """V-1.1: no constructor is exported to plugin code. AST checks: `Lineage` defines no
    alternate constructor (`__new__`, classmethod, staticmethod); no module of the plugin-facing
    surface (`trestle.workflow`, `trestle.plugin`) constructs one, and none of its module-level
    functions is annotated to return one; under `trestle/` only the host's run services (and the
    lane format's decoder) construct one."""
    values = ast.parse((REPO / "trestle" / "workflow" / "values.py").read_text(encoding="utf-8"))
    (lineage,) = (n for n in values.body if isinstance(n, ast.ClassDef) and n.name == "Lineage")
    for member in lineage.body:
        if isinstance(member, ast.FunctionDef):
            decorators = {ast.unparse(d) for d in member.decorator_list}
            assert not decorators & {"classmethod", "staticmethod"}, member.name
            assert member.name not in {"__new__", "__call__"}, member.name
    surface = _python_files(*PLUGIN_FACING)
    assert surface, PLUGIN_FACING
    for path in surface:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not _constructs_lineage(tree), path
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.returns is not None:
                assert "Lineage" not in ast.unparse(node.returns), (path, node.name)
    minted = {
        str(p.relative_to(REPO))
        for p in _python_files("trestle")
        if _constructs_lineage(ast.parse(p.read_text(encoding="utf-8")))
    }
    assert minted <= MINT_SITES, minted - MINT_SITES
    assert "trestle/child/run_services.py" in minted  # the host's mint site exists
