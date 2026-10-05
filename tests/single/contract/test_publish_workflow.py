"""L.SV-2.3: the validator extracts a workflow plugin's declared tree, the snapshot stores
`declaration.json`, its digest fills the MC-18 declared-tree slot, `load_declared_tree` is the
only reader, and the import allowlist admits the public `trestle.workflow` names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trestle.child.validate import forbidden_import_error
from trestle.common import codes
from trestle.common.plan import DeclaredTree
from trestle.common.types import PublishView, RequestOutcome
from trestle.server.main import create_kernel
from trestle.server.plugin_validate import DeclarationInvalid
from trestle.server.snapshots import load_declared_tree, materialize_snapshot

REPO = Path(__file__).resolve().parents[3]
ECHO = REPO / "examples" / "plugins" / "echo.py"

# The snapshot identity of examples/plugins/echo.py under runtime 0.3.0: no declared tree, so the
# MC-18 slot is empty and nothing changes for plain plugins. The runtime version is an identity
# ingredient (MC-18), so a version bump re-pins it; `wr-ckpt/core` (runtime 0.1.0) gave
# snap_625a117d0a2b667b.
ECHO_SNAPSHOT_ID = "snap_83cf30119b3b6f87"  # runtime 0.2.0 gave snap_6f04ffcc9585cf7d
ECHO_MANIFEST_SHA256 = "d14928d3a0aa984e4376fa4ce3fffd4552daaaa13cf3857586674a42cf9987d3"
PLAIN_SNAPSHOT_FILES = ["manifest.json", "plugin.py", "return_schema.json", "schema.json"]

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
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=BUDGET_S),
            max_attempts=1,
        )


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=60))


@trestle
def wf(ctx: Context, name: str = "x") -> dict[str, str]:
    return {"name": name}
"""


def _workflow(tmp_path: Path, name: str, *, budget_s: int = 30, extra: str = "") -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(WORKFLOW_SOURCE.replace("BUDGET_S", str(budget_s)) + extra, encoding="utf-8")
    return path


def _snap_dir(home: Path, snapshot_id: str) -> Path:
    return home / "snapshots" / snapshot_id


def test_pin_plain_plugin_snapshot_identity_unchanged(tmp_path: Path) -> None:
    snap = materialize_snapshot(ECHO, "echo", home=tmp_path)
    assert snap.snapshot_id == ECHO_SNAPSHOT_ID
    assert snap.manifest_sha256 == ECHO_MANIFEST_SHA256
    files = sorted(p.name for p in _snap_dir(tmp_path, snap.snapshot_id).iterdir())
    assert files == PLAIN_SNAPSHOT_FILES  # no declaration.json for a plain plugin
    assert load_declared_tree(snap) is None


def test_workflow_plugin_publishes_declaration_json(tmp_path: Path) -> None:
    snap = materialize_snapshot(_workflow(tmp_path, "wf"), "wf", home=tmp_path / "home")
    snap_dir = _snap_dir(tmp_path / "home", snap.snapshot_id)
    assert sorted(p.name for p in snap_dir.iterdir()) == sorted(
        [*PLAIN_SNAPSHOT_FILES, "declaration.json"]
    )
    tree = load_declared_tree(snap)
    assert isinstance(tree, DeclaredTree)
    assert tree.root == "unit" and tree.format_version == 1
    assert tree.nodes[""]["budget"] == 30.0
    assert (snap_dir / "declaration.json").read_text(encoding="utf-8") == tree.to_json()
    manifest = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["declaration_digest"] == tree.digest


@pytest.mark.proves("WR-PLAN-5", "WR-PLAN-5:declared-tree-in-identity", "A", "single", "PROC", "CI")
def test_declaration_edit_changes_identity(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = materialize_snapshot(_workflow(tmp_path, "wf"), "wf", home=home)
    again = materialize_snapshot(_workflow(tmp_path, "wf"), "wf", home=home)
    assert again.snapshot_id == first.snapshot_id  # identity is a function of the source
    edited = materialize_snapshot(_workflow(tmp_path, "wf", budget_s=310), "wf", home=home)
    assert edited.snapshot_id != first.snapshot_id
    tree_a, tree_b = load_declared_tree(first), load_declared_tree(edited)
    assert tree_a is not None and tree_b is not None and tree_a.digest != tree_b.digest
    assert tree_b.nodes[""]["budget"] == 310.0


@pytest.mark.parametrize(
    "line",
    [
        "from trestle.workflow import LeafDeclaration, WorkflowEntry",
        "from trestle.workflow import declarations",
        "from trestle.workflow.declarations import LeafDeclaration",
        "import trestle.workflow",
        "import trestle.workflow.declarations as decl",
        # the unit-author surface, public from L.SV-5.9 (PUBLIC_MODULES)
        "from trestle.workflow.loop import run_tree",
        "from trestle.workflow import loop",
        "from trestle.workflow.units import Acted, NoAction",
        "from trestle.workflow.values import Observation",
        "from trestle.workflow import ports",
        "from trestle.workflow.ports import ResourceReads",
    ],
)
def test_allowlist_admits_public_workflow_names(line: str) -> None:
    assert forbidden_import_error(line + "\n") is None, line


@pytest.mark.parametrize(
    "line",
    [
        "import trestle.workflow._x",
        "from trestle.workflow import _x",
        "from trestle.workflow._x import Y",
        "from trestle.workflow import _x as x, LeafDeclaration",
        "import trestle.workflow.extract",
        "from trestle.workflow import extract",
        "from trestle.workflow import facets",
        "from trestle.workflow.services import RunServices",
        "from trestle.workflow.join import join",
        "from trestle.workflow.decide import decide",
        "import trestle.common.plan",
    ],
)
def test_allowlist_refuses_private_and_internal_workflow_names(line: str) -> None:
    assert forbidden_import_error(line + "\n") is not None, line


def test_allowlist_admits_public_workflow_names_only() -> None:
    """The one node the plan names: public names in, `trestle.workflow._*` out."""
    assert forbidden_import_error("from trestle.workflow import LeafDeclaration\n") is None
    error = forbidden_import_error("import trestle.workflow._x\n")
    assert error is not None and "trestle.workflow._x" in error
    assert forbidden_import_error("from trestle.workflow import _x\n") is not None


RAISING_EXTRA = """

class Broken:
    def declare(self):
        raise RuntimeError("declare exploded")


ENTRY = WorkflowEntry(root="broken", units={"broken": Broken()}, deadline=timedelta(seconds=1))
"""


def test_declare_error_promotes_no_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    src = _workflow(tmp_path, "wf", extra=RAISING_EXTRA)
    bad_text = src.read_text(encoding="utf-8")
    good_text = WORKFLOW_SOURCE.replace("BUDGET_S", "30")
    with pytest.raises(DeclarationInvalid) as info:
        materialize_snapshot(src, "wf", home=home)
    assert "declare exploded" in str(info.value)
    snap_root = home / "snapshots"
    assert not snap_root.exists() or not any(snap_root.iterdir())

    # through publication: the stable code, and the previous snapshot keeps serving
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = create_kernel(home=tmp_path / "trestle", plugin_dirs=[plugin_dir], skip_recovery=True)
    good = kernel.control.publish_plugin(good_text)
    assert isinstance(good, PublishView)
    refused = kernel.control.publish_plugin(bad_text)
    assert isinstance(refused, RequestOutcome)
    assert (
        refused.code == codes.PUBLICATION_DECLARATION_INVALID == "publication.declaration_invalid"
    )
    assert "declare exploded" in refused.message
    desc = kernel.control.describe_plugin("wf")
    assert isinstance(desc, dict) and desc["snapshot_id"] == good.snapshot_id

    # two WorkflowEntry objects in one plugin is also a refusal, not a guess
    two = _workflow(
        tmp_path,
        "two",
        extra="\nENTRY_B = WorkflowEntry(root='unit', units={}, deadline=timedelta(seconds=1))\n",
    )
    with pytest.raises(DeclarationInvalid):
        materialize_snapshot(two, "two", home=tmp_path / "home2")
