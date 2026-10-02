"""M1 kernel skeleton smoke tests."""

from __future__ import annotations

import ast
from pathlib import Path

from trestle.common import codes
from trestle.common.types import RequestOutcome, RunView


def test_registry_lists_echo_plugin(kernel) -> None:
    catalog = kernel.control.list_plugins()
    names = {row["name"] for row in catalog["items"]}
    assert "echo" in names


def test_run_echo_returns_terminal_status_frame(kernel) -> None:
    result = kernel.control.run(plugin="echo", args={"message": "hi"}, wait_ms=5000)
    assert isinstance(result, RunView)
    assert result.run_id.startswith("r_")
    assert result.state == "succeeded"
    assert result.duration_ms is not None


def test_unknown_plugin_refusal_has_no_run_id(kernel) -> None:
    result = kernel.control.run(plugin="missing-plugin", args={})
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.PLUGIN_NOT_FOUND
    assert result.origin == "admission"
    assert not hasattr(result, "run_id")
    payload = result.to_dict()
    assert "run_id" not in payload


def test_wrapper_and_child_do_not_import_fastmcp() -> None:
    repo = Path(__file__).resolve().parents[1]
    for rel in (
        "trestle/wrapper/main.py",
        "trestle/wrapper/spawn.py",
        "trestle/wrapper/reactor.py",
        "trestle/child/main.py",
        "trestle/child/context.py",
    ):
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "fastmcp"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "fastmcp"
                assert not node.module.startswith("fastmcp.")


def test_list_plugins_tool_shape(kernel) -> None:
    catalog = kernel.control.list_plugins()
    assert "registry_version" in catalog
    assert "items" in catalog
    assert catalog["items"][0]["capability_class"] is None


def test_stub_tools_return_not_implemented(kernel) -> None:
    cancel = kernel.control.cancel("r_missing")
    assert cancel.code == codes.INVALID_HANDLE
    query = kernel.control.query("run", {"run_id": "r_missing"})
    assert isinstance(query, RequestOutcome)
    assert query.code == codes.INVALID_HANDLE
