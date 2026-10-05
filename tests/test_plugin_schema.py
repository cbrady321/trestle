"""JSON Schema from @trestle type hints (R-PLUG-1, R-PLUG-7–10, R-PLUG-22)."""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.canonical import canonical_json
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.main import create_kernel
from trestle.server.plugin_schema import (
    SUBSET_POINTER,
    SchemaError,
    input_schema_from_source,
    return_schema_from_source,
    schema_digest,
)
from trestle.server.snapshots import materialize_snapshot

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def publish_kernel(trestle_home: Path, tmp_path: Path) -> object:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    return create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)


def _plugin(body: str) -> str:
    header = (
        "from __future__ import annotations\n\n"
        "from trestle.plugin.surface import Context, trestle\n\n"
    )
    return header + textwrap.dedent(body).strip() + "\n"


def test_echo_schema_exposes_message() -> None:
    source = (REPO / "examples" / "plugins" / "echo.py").read_text(encoding="utf-8")
    schema = input_schema_from_source(source)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["message"] == {"type": "string"}
    assert "required" not in schema


def test_required_and_optional_params() -> None:
    source = _plugin(
        """
        @trestle
        def greet(ctx: Context, name: str, loud: bool = False) -> dict[str, str]:
            return {"name": name, "loud": str(loud)}
        """
    )
    schema = input_schema_from_source(source)
    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["properties"]["loud"] == {"type": "boolean"}
    assert schema["required"] == ["name"]


def test_containers_literal_optional() -> None:
    source = _plugin(
        """
        from typing import Literal

        @trestle
        def packed(
            ctx: Context,
            tags: list[str],
            mapping: dict[str, int],
            flags: set[str],
            mode: Literal["a", "b"] | None = None,
        ) -> dict[str, str]:
            return {"ok": "yes"}
        """
    )
    schema = input_schema_from_source(source)
    assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert schema["properties"]["mapping"] == {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }
    assert schema["properties"]["flags"]["uniqueItems"] is True
    assert schema["properties"]["mode"] == {"anyOf": [{"enum": ["a", "b"]}, {"type": "null"}]}


def test_same_file_dataclass_and_enum() -> None:
    source = textwrap.dedent(
        """
        from __future__ import annotations

        from dataclasses import dataclass
        from enum import Enum

        from trestle.plugin.surface import Context, trestle


        class Color(Enum):
            RED = "red"
            BLUE = "blue"


        @dataclass
        class Item:
            name: str
            color: Color = Color.RED


        @trestle
        def make(ctx: Context, item: Item) -> dict[str, str]:
            return {"name": item.name}
        """
    )
    schema = input_schema_from_source(source)
    item = schema["properties"]["item"]
    assert item["type"] == "object"
    assert item["properties"]["name"] == {"type": "string"}
    assert item["properties"]["color"] == {"enum": ["red", "blue"]}
    assert item["required"] == ["name"]
    assert schema["required"] == ["item"]


def test_rejects_bytes_with_subset_pointer() -> None:
    source = _plugin(
        """
        @trestle
        def blob(ctx: Context, data: bytes) -> dict[str, int]:
            return {"n": len(data)}
        """
    )
    with pytest.raises(SchemaError) as excinfo:
        input_schema_from_source(source)
    message = str(excinfo.value)
    assert "bytes" in message
    assert SUBSET_POINTER in message


@pytest.mark.parametrize("hint", ["list", "dict[int, str]", "tuple[int, str]"])
def test_rejects_unsupported_types(hint: str) -> None:
    source = _plugin(
        f"""
        @trestle
        def bad(ctx: Context, value: {hint}) -> dict[str, str]:
            return {{"ok": "no"}}
        """
    )
    with pytest.raises(SchemaError) as excinfo:
        input_schema_from_source(source)
    assert SUBSET_POINTER in str(excinfo.value)


def test_rejects_any() -> None:
    source = _plugin(
        """
        from typing import Any

        @trestle
        def bad(ctx: Context, value: Any) -> dict[str, str]:
            return {"ok": "no"}
        """
    )
    with pytest.raises(SchemaError) as excinfo:
        input_schema_from_source(source)
    assert "Any" in str(excinfo.value)
    assert SUBSET_POINTER in str(excinfo.value)


def test_schema_digest_is_not_empty_object_hash() -> None:
    source = (REPO / "examples" / "plugins" / "echo.py").read_text(encoding="utf-8")
    schema = input_schema_from_source(source)
    digest = schema_digest(schema)
    assert digest != hashlib.sha256(b"{}").hexdigest()
    assert digest == hashlib.sha256(canonical_json(schema)).hexdigest()


def test_materialize_snapshot_writes_schema_hash(tmp_path: Path) -> None:
    src = tmp_path / "echo.py"
    src.write_text(
        (REPO / "examples" / "plugins" / "echo.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    snap = materialize_snapshot(src, "echo", home=home)
    assert snap.schema_sha256 != hashlib.sha256(b"{}").hexdigest()
    schema_path = Path(snap.source_path).with_name("schema.json")
    assert schema_path.is_file()
    schema = input_schema_from_source(src.read_text(encoding="utf-8"))
    assert snap.schema_sha256 == schema_digest(schema)
    return_path = Path(snap.source_path).with_name("return_schema.json")
    assert return_path.is_file()


def test_echo_return_schema_is_string_map() -> None:
    source = (REPO / "examples" / "plugins" / "echo.py").read_text(encoding="utf-8")
    schema = return_schema_from_source(source)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] == {"type": "string"}


def test_rejects_bytes_return() -> None:
    source = _plugin(
        """
        @trestle
        def blob(ctx: Context) -> bytes:
            return b"no"
        """
    )
    with pytest.raises(SchemaError) as excinfo:
        return_schema_from_source(source)
    assert "bytes" in str(excinfo.value)
    assert SUBSET_POINTER in str(excinfo.value)


def test_rejects_missing_return_hint() -> None:
    source = _plugin(
        """
        @trestle
        def silent(ctx: Context, message: str = "x"):
            return {"message": message}
        """
    )
    with pytest.raises(SchemaError) as excinfo:
        return_schema_from_source(source)
    assert "return" in str(excinfo.value).lower()


def test_describe_plugin_echo_exposes_schemas(kernel) -> None:
    desc = kernel.control.describe_plugin("echo")
    assert isinstance(desc, dict)
    assert desc["name"] == "echo"
    props = desc["input_schema"]["properties"]
    assert "message" in props
    assert props["message"]["type"] == "string"
    assert desc["return_schema"]["type"] == "object"
    assert desc["return_schema"]["additionalProperties"] == {"type": "string"}


def test_list_plugins_has_no_input_schema(kernel) -> None:
    catalog = kernel.control.list_plugins()
    assert "input_schema" not in catalog
    assert "return_schema" not in catalog
    for row in catalog["items"]:
        assert "input_schema" not in row
        assert "return_schema" not in row


def test_publish_rejects_unsupported_return(publish_kernel) -> None:
    source = _plugin(
        """
        @trestle
        def blob(ctx: Context, message: str = "x") -> bytes:
            return message.encode()
        """
    )
    result = publish_kernel.control.publish_plugin(source)
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.PUBLICATION_VALIDATION_FAILED
    assert "bytes" in result.message
    assert SUBSET_POINTER in result.message


def test_publish_rejects_unsupported_type(publish_kernel) -> None:
    source = _plugin(
        """
        @trestle
        def blob(ctx: Context, data: bytes) -> dict[str, int]:
            return {"n": len(data)}
        """
    )
    result = publish_kernel.control.publish_plugin(source)
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.PUBLICATION_VALIDATION_FAILED
    assert result.origin == "publication"
    assert "bytes" in result.message
    assert SUBSET_POINTER in result.message


def test_publish_keeps_previous_when_types_are_invalid(publish_kernel) -> None:
    good = _plugin(
        """
        @trestle
        def greeter(ctx: Context, message: str = "hi") -> dict[str, str]:
            return {"message": message}
        """
    )
    first = publish_kernel.control.publish_plugin(good)
    assert isinstance(first, PublishView)
    bad = _plugin(
        """
        @trestle
        def greeter(ctx: Context, data: bytes) -> dict[str, str]:
            return {"n": str(len(data))}
        """
    )
    refused = publish_kernel.control.publish_plugin(bad)
    assert isinstance(refused, RequestOutcome)
    desc = publish_kernel.control.describe_plugin("greeter")
    assert isinstance(desc, dict)
    assert "message" in desc["input_schema"]["properties"]
    assert desc["source_sha256"] == first.source_sha256


def test_admit_echo_defaults_without_args(kernel) -> None:
    view = kernel.control.run(plugin="echo", args={}, wait_ms=5000)
    assert isinstance(view, RunView)
    assert view.state == "succeeded"


def test_admit_rejects_wrong_type(kernel) -> None:
    result = kernel.control.run(plugin="echo", args={"message": 1}, wait_ms=0)
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.INVALID_ARGS
    assert "run_id" not in result.to_dict()
    assert "describe_plugin" in result.message
    assert len(result.message) < 200


def test_admit_rejects_unexpected_arg(kernel) -> None:
    result = kernel.control.run(
        plugin="echo",
        args={"message": "ok", "extra": True},
        wait_ms=0,
    )
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.INVALID_ARGS
    assert "extra" in result.message


def test_admit_rejects_missing_required(publish_kernel) -> None:
    source = _plugin(
        """
        @trestle
        def greet(ctx: Context, name: str) -> dict[str, str]:
            return {"name": name}
        """
    )
    published = publish_kernel.control.publish_plugin(source)
    assert isinstance(published, PublishView)
    result = publish_kernel.control.run(plugin="greet", args={}, wait_ms=0)
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.INVALID_ARGS
    assert "name" in result.message
    assert "describe_plugin" in result.message
    assert "run_id" not in result.to_dict()
