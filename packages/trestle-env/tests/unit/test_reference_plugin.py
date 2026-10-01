"""The reference plugin publishes with the tree's own declaration (L.RB-0.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from trestle.server.main import create_kernel
from trestle.server.registry import load_declared_tree

from trestle_env import catalog_v0, schema, tree
from trestle_env.plugins import reference_env

PLUGINS = Path(reference_env.__file__).resolve().parent


@pytest.fixture
def published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    kernel = create_kernel(home=tmp_path / "home", plugin_dirs=[PLUGINS], skip_recovery=True)
    kernel.registry.refresh()
    return kernel


def test_the_reference_plugin_is_published_valid(published) -> None:  # type: ignore[no-untyped-def]
    items = published.registry.catalog().to_dict()["items"]
    assert [(row["name"], row["valid"]) for row in items] == [("reference_env", True)]


def test_the_published_input_schema_names_only_the_closed_arguments(published) -> None:  # type: ignore[no-untyped-def]
    described = published.control.describe_plugin("reference_env")
    assert isinstance(described, dict)
    schema_of = described["input_schema"]
    assert set(schema_of["properties"]) == {schema.ENV_ARG, schema.SERVICES_ARG}
    assert schema_of["required"] == [schema.ENV_ARG]
    assert schema_of["properties"][schema.SERVICES_ARG] == {
        "anyOf": [
            {"type": "array", "items": {"enum": list(catalog_v0.CATALOG_V0)}},
            {"type": "null"},
        ]
    }
    assert schema_of["properties"][schema.ENV_ARG] == {"type": "string"}
    assert schema_of["additionalProperties"] is False


def test_the_published_deadline_is_the_trees(published) -> None:  # type: ignore[no-untyped-def]
    described = published.control.describe_plugin("reference_env")
    assert described["deadline_s"] == tree.DEADLINE_S
    assert described["deadline_source"] == "declared"


def test_the_published_declaration_is_the_reference_tree(published) -> None:  # type: ignore[no-untyped-def]
    snap = published.registry.get("reference_env")
    assert snap is not None
    declared = load_declared_tree(snap)
    assert declared is not None
    assert declared.root == tree.ROOT_UNIT
    assert declared.nodes[""]["env_key_field"] == schema.ENV_ARG
    assert [c["name"] for c in declared.nodes[""]["children"]] == [tree.POSTGRES_UNIT]
    assert declared.nodes[tree.POSTGRES_UNIT]["postcondition"] == tree.POSTGRES_READY
