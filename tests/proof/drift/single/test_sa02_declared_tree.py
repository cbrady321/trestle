"""SA-02 in A-1 (L.SV-2.2): the declared tree's JSON is canonical, so a tree round-trips through
`declaration.json` to the same bytes and the same digest, and the digest covers exactly
`{format_version, root, nodes}`."""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.single.contract import declared_fixtures as fx
from trestle.common.plan import DeclaredTree, canonical_json
from trestle.workflow.extract import extract_declared_tree


@pytest.mark.parametrize("sa", ["SA-02"])
def test_declared_tree_json_roundtrip_digest_stable(sa: str) -> None:
    for entry in (fx.leaf_entry(), fx.composite_entry()):
        tree = extract_declared_tree(entry)
        text = tree.to_json()
        again = DeclaredTree.from_json(text)
        assert again == tree
        assert again.to_json() == text
        assert canonical_json(json.loads(text)) == text  # already canonical
        body = canonical_json(
            {"format_version": tree.format_version, "root": tree.root, "nodes": tree.nodes}
        )
        assert tree.digest == hashlib.sha256(body.encode("utf-8")).hexdigest()
        # any declared change moves the digest
    changed = extract_declared_tree(fx.leaf_entry(env_key_field="env"))
    assert changed.digest != extract_declared_tree(fx.leaf_entry()).digest
