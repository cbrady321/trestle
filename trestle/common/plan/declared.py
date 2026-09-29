"""The declared-tree wire format (MC-34; L.SV-2.2).

`snapshots/<id>/declaration.json` is the canonical JSON of one `DeclaredTree`:

    {"format_version": 1, "root": <unit name>, "nodes": {<path>: <node>}, "digest": <sha256 hex>}

`nodes` is keyed by canonical path (V-1.2): the root is the empty path `""`, a descendant is its
declared logical names joined by `/`. Every node carries `unit`, `compose`, `completion`, `repeat`,
`budget`, `env_key_field` and `declared_codes`; the rest depends on `compose`:

* `leaf`: `preconditions`, `postcondition`, `wait`, `resource_kind`, `may_touch`, `effects`,
  `retryable`, `remedies`, `max_attempts` (V-14 `LeafDeclaration`).
* `all`: `children` (a list of `{name, binding, path}`; `path` is `null` while the child is
  unresolved and the child's own path once a resolved descendant set is recorded, so descendants
  resolve under the same `format_version`), `concurrency`, `gates`, `identifier_sets`,
  `arg_bindings`.
* `choice`: `choice` (`logical_system`, `alternatives[{unit, realization, reachable_from,
  human_action, path}]`, `select_arg`, `fallback`, `readiness`).

Durations are seconds (JSON numbers), sets are sorted lists. The digest is the sha256 of the
canonical JSON of `{format_version, root, nodes}`. This module holds the format only; it imports
nothing but the standard library (host code reads it without importing `trestle.workflow`).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, final

FORMAT_VERSION = 1
ROOT_PATH = ""

_COMMON_KEYS = frozenset(
    {"unit", "compose", "completion", "repeat", "budget", "env_key_field", "declared_codes"}
)
_LEAF_KEYS = _COMMON_KEYS | {
    "preconditions",
    "postcondition",
    "wait",
    "resource_kind",
    "may_touch",
    "effects",
    "retryable",
    "remedies",
    "max_attempts",
}
_ALL_KEYS = _COMMON_KEYS | {"children", "concurrency", "gates", "identifier_sets", "arg_bindings"}
_CHOICE_KEYS = _COMMON_KEYS | {"choice"}
NODE_KEYS: Mapping[str, frozenset[str]] = {
    "leaf": _LEAF_KEYS,
    "all": _ALL_KEYS,
    "choice": _CHOICE_KEYS,
}
_CHILD_KEYS = frozenset({"name", "binding", "path"})
_TOP_KEYS = frozenset({"format_version", "root", "nodes", "digest"})


class UnknownDeclaredFormat(ValueError):
    """`format_version` is not one this reader understands."""


class DeclaredTreeInvalid(ValueError):
    """The text is not a well-formed declared tree (shape, digest or path reference)."""


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise DeclaredTreeInvalid("non_canonical_value")
        return value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise DeclaredTreeInvalid("non_string_key")
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    raise DeclaredTreeInvalid(f"non_json_value:{type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, UTF-8 characters unescaped: the same bytes in every process."""
    return json.dumps(_canonical(value), separators=(",", ":"), ensure_ascii=False)


def _digest(root: str, nodes: Mapping[str, Any], version: int = FORMAT_VERSION) -> str:
    body = canonical_json({"format_version": version, "root": root, "nodes": nodes})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@final
@dataclass(frozen=True, slots=True)
class DeclaredTree:
    format_version: int
    root: str
    nodes: Mapping[str, Mapping[str, Any]]
    digest: str

    @classmethod
    def build(cls, root: str, nodes: Mapping[str, Mapping[str, Any]]) -> DeclaredTree:
        """Validate the nodes and compute the digest."""
        _check_nodes(nodes)
        return cls(FORMAT_VERSION, root, nodes, _digest(root, nodes))

    def to_json(self) -> str:
        return canonical_json(
            {
                "format_version": self.format_version,
                "root": self.root,
                "nodes": self.nodes,
                "digest": self.digest,
            }
        )

    @classmethod
    def from_json(cls, text: str) -> DeclaredTree:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise DeclaredTreeInvalid("not_json") from exc
        if not isinstance(data, dict):
            raise DeclaredTreeInvalid("not_an_object")
        version = data.get("format_version")
        if version != FORMAT_VERSION or isinstance(version, bool):
            raise UnknownDeclaredFormat(f"format_version {version!r}")
        if set(data) != _TOP_KEYS:
            raise DeclaredTreeInvalid("keys")
        root, nodes, digest = data["root"], data["nodes"], data["digest"]
        if not isinstance(root, str) or not isinstance(nodes, dict) or not isinstance(digest, str):
            raise DeclaredTreeInvalid("types")
        _check_nodes(nodes)
        if digest != _digest(root, nodes, version):
            raise DeclaredTreeInvalid("digest_mismatch")
        return cls(version, root, nodes, digest)


def _check_nodes(nodes: Mapping[str, Mapping[str, Any]]) -> None:
    if ROOT_PATH not in nodes:
        raise DeclaredTreeInvalid("no_root_node")
    for path, node in nodes.items():
        if not isinstance(path, str) or not isinstance(node, Mapping):
            raise DeclaredTreeInvalid("node_shape")
        compose = node.get("compose")
        expected = NODE_KEYS.get(compose) if isinstance(compose, str) else None
        if expected is None:
            raise DeclaredTreeInvalid(f"compose:{path!r}")
        if set(node) != expected:
            raise DeclaredTreeInvalid(f"node_keys:{path!r}")
        refs: list[Any] = []
        if compose == "all":
            children = node["children"]
            if not isinstance(children, (list, tuple)):
                raise DeclaredTreeInvalid(f"children:{path!r}")
            for child in children:
                if not isinstance(child, Mapping) or set(child) != _CHILD_KEYS:
                    raise DeclaredTreeInvalid(f"child_keys:{path!r}")
                refs.append(child["path"])
        elif compose == "choice":
            choice = node["choice"]
            if not isinstance(choice, Mapping) or not isinstance(
                choice.get("alternatives"), (list, tuple)
            ):
                raise DeclaredTreeInvalid(f"choice:{path!r}")
            for alt in choice["alternatives"]:
                if not isinstance(alt, Mapping) or "path" not in alt:
                    raise DeclaredTreeInvalid(f"alternative:{path!r}")
                refs.append(alt["path"])
        for ref in refs:
            if ref is not None and ref not in nodes:
                raise DeclaredTreeInvalid(f"unknown_path:{ref!r}")
