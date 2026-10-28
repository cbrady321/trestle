"""L.CL-C2.3 (BFD-16; K-12, K-5): docs/plugins.md and the served guide topic that schema errors
point to (`trestle://guide/plugins/types`) carry the supported-types section, every accepted type
kind is listed in both, and the K-5 date refusal note is present."""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

from trestle.server import authoring, plugin_schema

REPO = Path(__file__).resolve().parents[3]
SECTION_HEADING = "Supported types"

# Each TypeNode class the resolver can produce, and each TScalar kind, with the
# words the documentation must use for it. A kind added to plugin_schema without an
# entry here fails `test_every_type_kind_has_a_documentation_rule`.
_NODE_TERMS: dict[str, tuple[str, ...]] = {
    "TLiteral": ("Literal",),
    "TEnum": ("Enum",),
    "TList": ("list", "Sequence"),
    "TSet": ("set", "frozenset"),
    "TDict": ("dict", "Mapping"),
    "TOptional": ("Optional",),
    "TUnion": ("Union",),
    "TObject": ("dataclass", "BaseModel", "TypedDict"),
}
_SCALAR_TERMS: dict[str, tuple[str, ...]] = {
    "str": ("str",),
    "int": ("int",),
    "float": ("float",),
    "bool": ("bool",),
    "null": ("None",),
    "path": ("Path",),
    "datetime": ("datetime",),
    "date": ("date",),
    "artifact_ref": ("ArtifactRef",),
}


def _section(text: str, heading: str) -> str:
    pattern = re.compile(rf"^## {re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    assert match is not None, f"no '## {heading}' section"
    rest = text[match.end() :]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _docs_section() -> str:
    """The section of docs/plugins.md, the full authoring reference."""
    path = REPO / "docs" / "plugins.md"
    return _section(path.read_text(encoding="utf-8"), SECTION_HEADING)


def _pointed_section() -> str:
    """The guide topic SUBSET_POINTER names: the whole topic is the section."""
    assert plugin_schema.SUBSET_POINTER == authoring.topic_uri("types")
    return authoring.guide_topic("types")


def _accepted_names() -> set[str]:
    """Every annotation name plugin_schema accepts, read from its own tables."""
    tables = (
        plugin_schema._SCALAR_NAMES,
        plugin_schema._PATH_NAMES,
        plugin_schema._DATETIME_NAMES,
        plugin_schema._DATE_NAMES,
        plugin_schema._ARTIFACT_NAMES,
        plugin_schema._LIST_NAMES,
        plugin_schema._SET_NAMES,
        plugin_schema._DICT_NAMES,
        plugin_schema._TUPLE_NAMES,
        plugin_schema._ENUM_BASES,
    )
    return {name for table in tables for name in table}


def _node_kinds() -> set[str]:
    return {cls.__name__ for cls in typing.get_args(plugin_schema.TypeNode.__value__)}


def _scalar_kinds() -> set[str]:
    kind_hint = typing.get_type_hints(plugin_schema.TScalar)["kind"]
    return set(typing.get_args(kind_hint))


def test_subset_pointer_names_the_types_topic() -> None:
    assert plugin_schema.SUBSET_POINTER == "trestle://guide/plugins/types"


def test_every_type_kind_has_a_documentation_rule() -> None:
    assert _node_kinds() - {"TScalar"} == set(_NODE_TERMS)
    assert _scalar_kinds() == set(_SCALAR_TERMS)


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-12", "core", "core", "INSPECT", "CI")
def test_every_accepted_type_documented() -> None:
    _check_section(_docs_section())


def test_every_accepted_type_in_the_guide() -> None:
    _check_section(_pointed_section())


def _check_section(section: str) -> None:
    words = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", section))
    missing = sorted(_accepted_names() - words)
    assert not missing, f"accepted names absent from the '{SECTION_HEADING}' section: {missing}"
    for terms in (*_NODE_TERMS.values(), *_SCALAR_TERMS.values()):
        for term in terms:
            assert term in words, f"type term {term!r} absent from the section"
    for base in (plugin_schema._TYPED_DICT_BASES, plugin_schema._PYDANTIC_BASES):
        assert base <= words
    for annotation in ("Annotated", "Union", "Optional", "Literal", "TypeAlias"):
        assert annotation in words


def test_schema_errors_point_at_a_section_that_exists() -> None:
    with pytest.raises(plugin_schema.SchemaError) as excinfo:
        plugin_schema.input_schema_from_source(
            "from trestle.plugin.surface import trestle\n\n"
            "@trestle\ndef f(ctx, data: bytes) -> dict[str, int]:\n    return {}\n"
        )
    assert plugin_schema.SUBSET_POINTER in str(excinfo.value)
    assert _pointed_section().strip()


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-5", "core", "core", "INSPECT", "CI")
def test_k5_date_refusal_note_present() -> None:
    plugins = (REPO / "docs" / "plugins.md").read_text(encoding="utf-8")
    note = _section(plugins, SECTION_HEADING)
    assert "### Dates and datetimes" in note
    for needle in ("naive", "timezone", "admission.invalid_args", "before a run id"):
        assert needle in note, needle
    agents = (REPO / "docs" / "agents.md").read_text(encoding="utf-8")
    assert "naive" in agents and "admission.invalid_args" in agents
