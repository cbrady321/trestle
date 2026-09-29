"""L.SV-2.2: the declared-tree wire format (MC-34) and one-vertex extraction."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from tests.single.contract import declared_fixtures as fx
from trestle.common.plan import ROOT_PATH, DeclaredTree, DeclaredTreeInvalid, UnknownDeclaredFormat
from trestle.workflow import WorkflowEntry
from trestle.workflow.extract import ExtractionRefused, extract_declared_tree

ROOT = Path(__file__).resolve().parents[3]

_CHILD_PROGRAM = """
import sys
from tests.single.contract import declared_fixtures as fx
from trestle.workflow.extract import extract_declared_tree
sys.stdout.write(extract_declared_tree(fx.leaf_entry()).to_json())
"""


def _emit(hashseed: str) -> str:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": hashseed}
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return proc.stdout


def test_leaf_root_extracts_one_vertex_deterministic() -> None:
    tree = extract_declared_tree(fx.leaf_entry())
    assert tree.format_version == 1
    assert tree.root == "svc"
    assert list(tree.nodes) == [ROOT_PATH]
    node = tree.nodes[ROOT_PATH]
    assert (
        node["compose"] == "leaf" and node["repeat"] == "safe" and node["completion"] == "observed"
    )
    assert node["preconditions"] == ["net_up", "disk_free"] and node["postcondition"] == "svc_ready"
    assert node["may_touch"] == ["network", "service", "volume"]
    assert node["declared_codes"] == ["net.flaky", "tool.busy"]
    assert node["wait"] == {"poll_every": 2.0, "backoff": 1.5, "max_wait": 60.0}
    assert node["effects"][0]["host_sections"] == ["demo_credential", "toolchain_installs"]
    assert node["max_attempts"] == 3 and node["env_key_field"] is None
    assert "children" not in node
    # two processes, different hash seeds (set order differs): byte-identical JSON and digest
    first, second = _emit("1"), _emit("4242")
    assert first == second == tree.to_json()
    assert json.loads(first)["digest"] == tree.digest


def test_composite_root_recorded_with_unresolved_children() -> None:
    tree = extract_declared_tree(fx.composite_entry())
    assert list(tree.nodes) == [ROOT_PATH]  # only the root is recorded; descendants are TR-0's
    node = tree.nodes[ROOT_PATH]
    assert node["compose"] == "all" and node["concurrency"] == 2 and node["gates"] == ["db"]
    assert [c["path"] for c in node["children"]] == [None, None]
    assert [c["name"] for c in node["children"]] == ["db", "api1"]
    assert node["children"][1]["binding"] == {
        "unit": "api",
        "params": {},
        "needs": ["db"],
        "vantage": "container",
    }
    assert node["identifier_sets"] == {"systems": ["api", "db"]}
    assert node["arg_bindings"] == [
        {"arg": "systems", "identifier_set": "systems", "filters_children": True}
    ]
    choice_tree = extract_declared_tree(
        WorkflowEntry(root="pick", units={"pick": fx.choice_declaration()}, deadline=timedelta(1))
    )
    alts = choice_tree.nodes[ROOT_PATH]["choice"]["alternatives"]
    assert [a["path"] for a in alts] == [None, None]
    assert alts[1]["human_action"] == "start it in the IDE"


def _resolved_three_level() -> dict[str, dict[str, object]]:
    """A resolved three-level tree (root all, mid all, leaf) from extractor output."""
    root = copy.deepcopy(dict(extract_declared_tree(fx.composite_entry()).nodes[ROOT_PATH]))
    mid = copy.deepcopy(root)
    mid["unit"] = "mid"
    mid["children"] = [
        {
            "name": "leaf",
            "binding": {"unit": "svc", "params": {}, "needs": [], "vantage": "host"},
            "path": "mid/leaf",
        }
    ]
    root["children"] = [
        {
            "name": "mid",
            "binding": {"unit": "mid", "params": {}, "needs": [], "vantage": "host"},
            "path": "mid",
        }
    ]
    leaf = copy.deepcopy(dict(extract_declared_tree(fx.leaf_entry()).nodes[ROOT_PATH]))
    return {ROOT_PATH: root, "mid": mid, "mid/leaf": leaf}


def test_resolved_children_parse_without_format_bump() -> None:
    tree = DeclaredTree.build("stack", _resolved_three_level())
    assert tree.format_version == 1
    loaded = DeclaredTree.from_json(tree.to_json())
    assert loaded == tree and loaded.format_version == 1
    assert loaded.nodes["mid"]["children"][0]["path"] == "mid/leaf"
    # a path that resolves to no node is refused; a newer format is refused by name
    broken = _resolved_three_level()
    broken["mid"]["children"][0]["path"] = "mid/ghost"  # type: ignore[index]
    with pytest.raises(DeclaredTreeInvalid):
        DeclaredTree.build("stack", broken)
    data = json.loads(tree.to_json())
    data["format_version"] = 2
    with pytest.raises(UnknownDeclaredFormat):
        DeclaredTree.from_json(json.dumps(data))
    data["format_version"] = 1
    data["digest"] = "0" * 64
    with pytest.raises(DeclaredTreeInvalid):
        DeclaredTree.from_json(json.dumps(data))


def test_declare_raising_refuses() -> None:
    entry = WorkflowEntry(root="bad", units={"bad": fx.RaisingLeaf()}, deadline=timedelta(1))
    with pytest.raises(ExtractionRefused) as info:
        extract_declared_tree(entry)
    assert info.value.subject == "bad" and "RuntimeError" in info.value.message
    assert info.value.code == "DECLARATION_INVALID"
    for bad in (
        WorkflowEntry(root="missing", units={}, deadline=timedelta(1)),
        WorkflowEntry(root="x", units={"x": object()}, deadline=timedelta(1)),
    ):
        with pytest.raises(ExtractionRefused):
            extract_declared_tree(bad)
