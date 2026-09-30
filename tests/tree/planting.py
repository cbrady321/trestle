"""Plant a declaration below publication (L.TR-1.1; DAG AM-3).

L.TR-0.4 refuses a cycle, a dangling name, an unresolved unit and a literal conflict at
publication, so a published snapshot never carries one and admission's own reach for them
(V-11's defensive second reach) has no input the validator lets through. A test that means to
drive admission with such a tree publishes the repaired source and then rewrites the snapshot's
`declaration.json` (MC-34) through `DeclaredTree.build`, so the file stays a well-formed,
correctly-digested declaration: admission reads it exactly as it reads a published one."""

from __future__ import annotations

import copy
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

from trestle.common.plan.declared import DeclaredTree
from trestle.common.types import PluginSnapshot
from trestle.server.snapshots import DECLARATION_FILE

Nodes = MutableMapping[str, MutableMapping[str, Any]]


def plant(snap: PluginSnapshot, mutate: Callable[[Nodes], None]) -> DeclaredTree:
    """Rewrite `snap`'s declaration: `mutate` edits a deep copy of the nodes in place. Returns the
    tree now on disk."""
    path = Path(snap.source_path).with_name(DECLARATION_FILE)
    tree = DeclaredTree.from_json(path.read_text(encoding="utf-8"))
    nodes: Nodes = copy.deepcopy(dict(tree.nodes))  # type: ignore[assignment]
    mutate(nodes)
    planted = DeclaredTree.build(tree.root, nodes)
    path.write_text(planted.to_json(), encoding="utf-8")
    return planted


def child(nodes: Nodes, parent: str, name: str) -> MutableMapping[str, Any]:
    """The child entry called `name` of the composite at `parent` (canonical path)."""
    for entry in nodes[parent]["children"]:
        if entry["name"] == name:
            return entry  # type: ignore[no-any-return]
    raise KeyError(name)
