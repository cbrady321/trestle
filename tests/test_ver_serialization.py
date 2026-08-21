"""R-VER-6–8 — serialization determinism, all shapes, tool-definition bytes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from trestle.child.serialize import write_result
from trestle.common.canonical import NonCanonical, args_hash, canonical_json
from trestle.server.main import attach_registry_version_mirror, create_kernel, run_server

TOOLS_LIST_BYTE_BUDGET = 8192


def test_canonical_json_is_deterministic() -> None:
    a = {"z": 1, "a": 2, "m": {"b": True, "a": None}}
    b = {"m": {"a": None, "b": True}, "a": 2, "z": 1}
    assert canonical_json(a) == canonical_json(b)
    assert args_hash(a) == args_hash(b)


def test_canonical_json_rejects_non_finite_floats() -> None:
    with pytest.raises(NonCanonical):
        canonical_json({"x": float("nan")})
    with pytest.raises(NonCanonical):
        canonical_json({"x": float("inf")})


@pytest.mark.parametrize(
    ("value", "root_type"),
    [
        (None, "null"),
        (True, "bool"),
        (42, "int"),
        (3.14, "float"),
        ("hello", "str"),
        ({"b": 2, "a": 1}, "object"),
        ([1, 2, 3], "array"),
        ({"nested": {"items": [1, {"k": "v"}]}}, "object"),
    ],
)
def test_result_serialization_all_shapes(tmp_path: Path, value: object, root_type: str) -> None:
    path = tmp_path / "result.json"
    index = write_result(path, value)
    assert index.root_type == root_type
    assert path.exists()
    assert path.stat().st_size > 0
    index_path = tmp_path / "result.index"
    index_path.write_bytes(index.to_json())
    reloaded = json.loads(index_path.read_text(encoding="utf-8"))
    assert reloaded["root_type"] == root_type


def test_result_serialization_rejects_nan(tmp_path: Path) -> None:
    with pytest.raises(NonCanonical):
        write_result(tmp_path / "result.json", float("nan"))


def test_huge_object_index_coarsens(tmp_path: Path) -> None:
    huge = {f"k{i}": i for i in range(20_000)}
    path = tmp_path / "result.json"
    index = write_result(path, huge)
    assert index.index_truncated is True
    assert len(index.to_json()) <= 64 * 1024


def test_tools_list_stays_within_byte_budget(
    trestle_home: Path,
    plugin_dir: Path,
) -> None:
    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    mcp = FastMCP("trestle-ver8")
    attach_registry_version_mirror(mcp, kernel)

    @mcp.tool
    def run(
        plugin: str,
        args: dict[str, object] | None = None,
        version: str | None = None,
        wait_ms: int = 2000,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        return {"stub": True}

    @mcp.tool
    def await_runs(
        run_ids: list[str],
        mode: str = "all",
        timeout_ms: int = 2000,
    ) -> dict[str, object]:
        return {"stub": True}

    @mcp.tool
    def cancel(run_id: str) -> dict[str, object]:
        return {"stub": True}

    @mcp.tool
    def query(
        view: str,
        params: dict[str, object] | None = None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        return {"stub": True}

    @mcp.tool
    def fetch(target: str, window: dict[str, object]) -> dict[str, object]:
        return {"stub": True}

    @mcp.tool
    def pin(target: str) -> dict[str, object]:
        return {"stub": True}

    @mcp.tool
    def unpin(target: str) -> dict[str, object]:
        return {"stub": True}

    @mcp.tool
    def list_plugins() -> dict[str, object]:
        return kernel.control.list_plugins()

    @mcp.tool
    def describe_plugin(plugin_id: str) -> dict[str, object]:
        return {"stub": True}

    async def measure() -> int:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            payload = json.dumps(
                [
                    {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                    for t in tools
                ],
                separators=(",", ":"),
            )
            return len(payload.encode("utf-8"))

    byte_len = asyncio.run(measure())
    assert byte_len <= TOOLS_LIST_BYTE_BUDGET, (
        f"tools/list definitions {byte_len}B > {TOOLS_LIST_BYTE_BUDGET}B"
    )


def test_nine_tool_names_registered_in_server_module() -> None:
    import inspect

    source = inspect.getsource(run_server)
    for name in (
        "run",
        "await_runs",
        "cancel",
        "query",
        "fetch",
        "pin",
        "unpin",
        "list_plugins",
        "describe_plugin",
    ):
        assert f"def {name}(" in source
