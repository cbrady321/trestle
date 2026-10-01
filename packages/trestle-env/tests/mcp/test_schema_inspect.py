"""The published input schema holds no free-form field (L.RB-1.2; WR-AUTH-3).

A request can name only catalog identifiers and the environment key. Inspected through
`describe_plugin`, the way an agent reads the schema: every string leaf is an enum, a const, or an
identifier bound to a declared identifier set listed at `valid_listed_at` (L.RB-1.1); the one other
string is the environment key, the root declaration's `env_key_field`, an opaque key the host
compares as bytes and never runs, opens or resolves. A snapshot of the schema is kept, so a field
added later is a reviewed change."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from trestle.server.main import Kernel, create_kernel
from trestle.server.registry import load_declared_tree

from trestle_env import schema, tree
from trestle_env.plugins import reference_env

PLUGINS = Path(reference_env.__file__).resolve().parent
SNAPSHOT = Path(__file__).with_name("schema_snapshot.json")

# names a free-form field would carry: none may appear in the published schema
FORBIDDEN_FIELDS = (
    "command",
    "cmd",
    "argv",
    "script",
    "shell",
    "path",
    "file",
    "url",
    "uri",
    "host",
    "port",
    "image",
    "compose",
    "yaml",
    "environment",
    "envvar",
    "password",
    "secret",
    "token",
    "credential",
    "key",
)


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[PLUGINS], skip_recovery=True)
    built.registry.refresh()
    return built


def string_leaves(node: Any, where: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every `{"type": "string"}` leaf of a schema, with the property path that reaches it (an
    array's items are `<path>[]`); enum and const leaves are closed and never listed."""
    found: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(node, dict):
        return found
    if "enum" in node or "const" in node:
        return found
    if node.get("type") == "string":
        found.append((where, node))
    for name, child in (node.get("properties") or {}).items():
        found += string_leaves(child, f"{where}.{name}" if where else name)
    if "items" in node:
        found += string_leaves(node["items"], f"{where}[]")
    for option in node.get("anyOf") or ():
        found += string_leaves(option, where)
    return found


def free_form(
    input_schema: dict[str, Any], bound: dict[str, str], env_key: str | None
) -> list[str]:
    """The string leaves that are neither closed nor a bound identifier nor the environment key,
    plus every field named like a command, path, URL, environment variable or credential."""
    problems = []
    for where, _leaf in string_leaves(input_schema):
        argument = where.removesuffix("[]")
        if argument in bound or argument == env_key:
            continue
        problems.append(f"{where}: a free-form string")
    for name in input_schema.get("properties", {}):
        lowered = name.lower()
        if any(word in lowered for word in FORBIDDEN_FIELDS) and name != env_key:
            problems.append(f"{name}: named like a free-form field")
    if input_schema.get("additionalProperties") is not False:
        problems.append("additionalProperties is not closed")
    return problems


def declared(kernel: Kernel) -> tuple[dict[str, Any], dict[str, str], str | None]:
    described = kernel.control.describe_plugin("reference_env")
    assert isinstance(described, dict)
    snap = kernel.registry.get("reference_env")
    assert snap is not None
    root = load_declared_tree(snap).nodes[""]  # type: ignore[union-attr]
    bound = {b["arg"]: b["identifier_set"] for b in root["arg_bindings"]}
    for name in bound.values():
        assert name in root["identifier_sets"]  # every bound set is declared (valid_listed_at)
    return described["input_schema"], bound, root["env_key_field"]


@pytest.mark.proves("WR-AUTH-3", "WR-AUTH-3:schema-no-free-form", "B", "B", "MCP+INSPECT", "CI")
def test_input_schema_has_only_closed_identifiers(kernel: Kernel) -> None:
    input_schema, bound, env_key = declared(kernel)
    assert free_form(input_schema, bound, env_key) == []
    # every identifier field of the schema is bound to a declared set, and the reverse
    identifier_fields = {where.removesuffix("[]") for where, _ in string_leaves(input_schema)} - {
        env_key
    }
    assert (
        identifier_fields
        == set(bound)
        == {
            schema.SERVICES_ARG,
            schema.TESTS_ARG,
            schema.OVERRIDES_ARG,
        }
    )
    assert bound == {
        schema.SERVICES_ARG: tree.SERVICES_SET,
        schema.TESTS_ARG: tree.TESTS_SET,
        schema.OVERRIDES_ARG: tree.OVERRIDES_SET,
    }
    # every identifier list is unique-item, and the environment key is the declared one
    for name in bound:
        listed = next(o for o in input_schema["properties"][name]["anyOf"] if o["type"] == "array")
        assert listed["uniqueItems"] is True
    assert env_key == schema.ENV_ARG


def test_the_published_schema_matches_its_snapshot(kernel: Kernel) -> None:
    input_schema, _, _ = declared(kernel)
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8")) == input_schema


def test_the_inspection_finds_planted_free_form_fields() -> None:
    bound = {"services": "services"}
    good = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "env": {"type": "string"},
            "services": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "mode": {"enum": ["a", "b"]},
        },
    }
    assert free_form(good, bound, "env") == []
    for planted, expected in (
        ("note", "note: a free-form string"),
        ("command", "command: a free-form string"),
    ):
        bad = json.loads(json.dumps(good))
        bad["properties"][planted] = {"type": "string"}
        assert expected in free_form(bad, bound, "env")
    named = json.loads(json.dumps(good))
    named["properties"]["compose_file"] = {"enum": ["x"]}
    assert "compose_file: named like a free-form field" in free_form(named, bound, "env")
    named = json.loads(json.dumps(good))
    named["additionalProperties"] = True
    assert free_form(named, bound, "env") == ["additionalProperties is not closed"]
    unbound = json.loads(json.dumps(good))
    unbound["properties"]["tests"] = {"type": "array", "items": {"type": "string"}}
    assert "tests[]: a free-form string" in free_form(unbound, bound, "env")
