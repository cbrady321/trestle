"""L.TR-0.3: the resolved declared tree is folded into the identity digest (MC-18's declared-tree
slot), `WR-UNIT-8:descendant-edit-identity`.

The slot is `DeclaredTree.digest`, the sha256 over `{format_version, root, nodes}` (MC-34). Since
L.TR-0.2 records every descendant as a node, a descendant declaration edit moves that digest and so
the published snapshot's identity, a non-declaration edit moves source identity only, and a plain
plugin or a leaf root is byte-identical to `wr-ckpt/single`. No admission is attempted here: a
multi-vertex root is refused until TR-L (MB3-03), so the admitted-identity case and
`WR-PLAN-5:tree` are L.TR-L.4's."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from trestle.server.snapshots import load_declared_tree, materialize_snapshot
from trestle.workflow.extract import extract_declared_tree

REPO = Path(__file__).resolve().parents[2]
ECHO = REPO / "examples" / "plugins" / "echo.py"
SPINE_LEAF = REPO / "tests" / "fixtures" / "workflows" / "spine_leaf.py"
THREE_LEVEL = REPO / "tests" / "fixtures" / "trees" / "three_level.py"

# The snapshot identities of the two bare plugins as `wr-ckpt/single` (the A-1 bundle head)
# produces them: one declares no tree (echo), one a leaf-root tree (spine_leaf).
ECHO_SNAPSHOT_ID = "snap_625a117d0a2b667b"
SPINE_LEAF_DECLARATION_DIGEST = "df1c29c802b30eeb7a6869107b3f55690eb3192d19e937193c45279a2210efda"

DB_BUDGET = 'leaf("db")'  # the descendant `three_level` declares at depth 3

proves_descendant_edit = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:descendant-edit-identity", "A", "tree", "LOGIC", "CI"
)


# A declared package that supplies the descendant `worker`: the plugin imports its `ENTRY`.
PACKAGE_UNITS = """\
# COMMENT
from datetime import timedelta

from trestle.workflow import (
    AllDeclaration, ChildBinding, CompletionSource, Compose, LeafDeclaration, LoopFlags, Repeat,
    WaitPolicy, WorkflowEntry,
)


class Unit:
    def declare(self):
        return LeafDeclaration(
            unit="worker",
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=10)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=BUDGET),
            max_attempts=1,
        )


ROOT = AllDeclaration(
    unit="pair",
    flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
    children=(ChildBinding(unit="worker", params={}, needs=()),),
    concurrency=1,
    budget=timedelta(seconds=200),
    identifier_sets={},
    arg_bindings=(),
    env_key_field=None,
)
ENTRY = WorkflowEntry(
    root="pair", units={"pair": ROOT, "worker": Unit()}, deadline=timedelta(seconds=300)
)
"""


def _publish(source: str, tmp_path: Path, home: str = "home"):  # type: ignore[no-untyped-def]
    path = tmp_path / "three_level.py"
    path.write_text(source, encoding="utf-8")
    snap = materialize_snapshot(path, "three_level", home=tmp_path / home)
    tree = load_declared_tree(snap)
    assert tree is not None
    manifest = json.loads(Path(snap.source_path).with_name("manifest.json").read_text())
    assert manifest["declaration_digest"] == tree.digest  # the declared-tree slot
    return snap, tree


def _three_level(**edits: str) -> str:
    source = THREE_LEVEL.read_text(encoding="utf-8")
    for old, new in edits.items():
        assert old in source
        source = source.replace(old, new)
    return source


@proves_descendant_edit
def test_declared_tree_digest_covers_descendants(tmp_path: Path) -> None:
    base = THREE_LEVEL.read_text(encoding="utf-8")
    edited = base.replace('"db": leaf("db"),', '"db": leaf("db", budget=45),')
    assert edited != base
    first, first_tree = _publish(base, tmp_path)
    second, second_tree = _publish(edited, tmp_path, home="home-2")
    # only a descendant (data/db) changed: its node, the tree digest and the identity all moved
    assert first_tree.nodes["data/db"] != second_tree.nodes["data/db"]
    assert first_tree.nodes[""] == second_tree.nodes[""]  # the root's own declaration is the same
    assert first_tree.digest != second_tree.digest
    assert first.snapshot_id != second.snapshot_id
    assert first.manifest_sha256 != second.manifest_sha256
    # a descendant declaration is covered wherever it sits, not only at depth 2
    deeper = base.replace('"web": leaf("web"),', '"web": leaf("web", pre=("db_ready",)),')
    _, deeper_tree = _publish(deeper, tmp_path, home="home-3")
    assert deeper_tree.digest not in {first_tree.digest, second_tree.digest}


@proves_descendant_edit
def test_comment_edit_keeps_tree_digest(tmp_path: Path) -> None:
    base = THREE_LEVEL.read_text(encoding="utf-8")
    first, first_tree = _publish(base, tmp_path)
    second, second_tree = _publish(base + "\n# a comment, not a declaration\n", tmp_path, "home-2")
    assert first_tree.digest == second_tree.digest  # the declared tree did not move
    assert first_tree.to_json() == second_tree.to_json()
    assert first.source_sha256 != second.source_sha256  # ... only the source identity did
    assert first.snapshot_id != second.snapshot_id


@proves_descendant_edit
def test_package_descendant_edit_moves_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A descendant supplied by a declared package: editing its declaration moves the tree digest
    (and so the identity); a non-declaration edit of the package moves the package digest only."""
    lib = tmp_path / "lib"
    lib.mkdir()
    module = lib / "tree_pkg_units.py"
    monkeypatch.setenv("PYTHONPATH", str(lib) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")  # a rewritten module is never stale
    monkeypatch.syspath_prepend(str(lib))

    def publish(budget: int, comment: str, home: str):  # type: ignore[no-untyped-def]
        module.write_text(
            PACKAGE_UNITS.replace("BUDGET", str(budget)).replace("COMMENT", comment),
            encoding="utf-8",
        )
        plugin = tmp_path / "pkg_tree.py"
        plugin.write_text(
            "from tree_pkg_units import ENTRY, Unit  # noqa: F401\n"
            "from trestle.plugin import Context, trestle\n\n\n"
            '@trestle(deadline=300, packages=["tree_pkg_units"])\n'
            'def pkg_tree(ctx: Context) -> dict[str, str]:\n    return {"fixture": "pkg_tree"}\n',
            encoding="utf-8",
        )
        snap = materialize_snapshot(plugin, "pkg_tree", home=tmp_path / home)
        tree = load_declared_tree(snap)
        assert tree is not None
        return snap, tree

    base, base_tree = publish(30, "one", "h1")
    assert sorted(base_tree.nodes) == ["", "worker"]  # the package supplies the descendant
    edited, edited_tree = publish(145, "one", "h2")
    assert base_tree.nodes["worker"] != edited_tree.nodes["worker"]
    assert base_tree.digest != edited_tree.digest
    assert base.snapshot_id != edited.snapshot_id

    commented, commented_tree = publish(30, "a longer comment", "h3")
    assert commented_tree.digest == base_tree.digest  # a comment is not a declaration
    assert commented.snapshot_id != base.snapshot_id  # the package's digest is part of identity


@proves_descendant_edit
def test_bare_plugin_identity_unchanged(tmp_path: Path) -> None:
    echo = materialize_snapshot(ECHO, "echo", home=tmp_path / "home")
    assert echo.snapshot_id == ECHO_SNAPSHOT_ID
    assert load_declared_tree(echo) is None  # a plain plugin declares no tree
    spine = materialize_snapshot(SPINE_LEAF, "spine_leaf", home=tmp_path / "home")
    tree = load_declared_tree(spine)
    assert tree is not None and list(tree.nodes) == [""]  # a leaf root is one node
    assert tree.digest == SPINE_LEAF_DECLARATION_DIGEST
    assert re.fullmatch(r"snap_[0-9a-f]{16}", spine.snapshot_id)
    # the leaf root's identity is a pure function of its bytes: publishing it again gives it back
    again = materialize_snapshot(SPINE_LEAF, "spine_leaf", home=tmp_path / "home-2")
    assert (again.snapshot_id, again.manifest_sha256) == (spine.snapshot_id, spine.manifest_sha256)
    assert extract_declared_tree(_entry_of(SPINE_LEAF)).digest == tree.digest


def _entry_of(path: Path):  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"identity_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ENTRY


def test_descendant_edit_moves_published_identity_proc(tmp_path: Path) -> None:
    """Supporting evidence (PROC, no label): `three_level` published through the real validator
    subprocess, a descendant declaration edited and republished into the same home: the new
    snapshot's declared-tree slot and identity differ from the previous snapshot's."""
    home = tmp_path / "home"
    plugin = tmp_path / "three_level.py"
    plugin.write_text(THREE_LEVEL.read_text(encoding="utf-8"), encoding="utf-8")
    first = materialize_snapshot(plugin, "three_level", home=home)
    first_tree = load_declared_tree(first)
    plugin.write_text(
        _three_level(**{'"cache": leaf("cache"),': '"cache": leaf("cache", budget=50),'})
    )
    second = materialize_snapshot(plugin, "three_level", home=home)
    second_tree = load_declared_tree(second)
    assert first_tree is not None and second_tree is not None
    assert first_tree.digest != second_tree.digest
    assert first.snapshot_id != second.snapshot_id
    assert (home / "snapshots" / first.snapshot_id / "declaration.json").is_file()  # both kept
    assert (home / "snapshots" / second.snapshot_id / "declaration.json").is_file()
