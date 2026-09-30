"""Declaration extraction (L.SV-2.2, L.TR-0.2): walk a `WorkflowEntry` and produce the
`DeclaredTree`.

Runs in the publication throwaway validator, the only place plugin code is imported at publish
time, and in the child at run start (B1-O3), so both derive the same tree. A leaf root extracts to
one node. A composite root is resolved over its descendants (MC-34, no format bump): one node per
declaration path with exactly the leaf/all/choice field set, and every `children[].path` /
`alternatives[].path` the path of the node it names (V-1.2: a logical node is `(unit, name)`; its
canonical path is its first occurrence in depth-first declaration order, the names joined by `/`,
and every other occurrence is an alias that names the same path; an alternative's name is its
choice's, so its path is `<choice path>/<unit>` and the same unit offered by two choices is two
nodes). A child whose unit the entry does not resolve keeps `path: null`, an unresolved name that
admission refuses (L.TR-1.1). Two distinct logical nodes that would share one path (one composite
binding one name to two units) get a `#<n>` suffix on the later one, so the compiler sees two
nodes under one name and refuses it (`DECLARATION_CONFLICT`).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from trestle.common.plan.declared import ROOT_PATH, DeclaredTree, DeclaredTreeInvalid
from trestle.workflow.declarations import (
    AllDeclaration,
    ChoiceNode,
    LeafDeclaration,
    LoopFlags,
    WorkflowEntry,
)
from trestle.workflow.registration import check_declaration

# The spelling of the wire code `publication.declaration_invalid` (L.SV-2.3 adds the constant to
# `trestle/common/codes.py`; `trestle.workflow` may not import it).
DECLARATION_INVALID = "DECLARATION_INVALID"
REFUSAL_TEXT_MAX = 200  # V-13


class ExtractionRefused(Exception):
    """Extraction produced no declaration: `declare()` raised, returned the wrong type, the root
    is unresolved or a declared value is not encodable (B2-C1: never a partial declaration)."""

    def __init__(self, subject: str, message: str, code: str = DECLARATION_INVALID) -> None:
        self.code = code
        self.subject = subject[:REFUSAL_TEXT_MAX]
        self.message = message[:REFUSAL_TEXT_MAX]
        super().__init__(f"{self.code}: {self.subject}: {self.message}")


def _secs(value: timedelta | None) -> float | None:
    return None if value is None else value.total_seconds()


def _common(
    decl: LeafDeclaration | AllDeclaration | ChoiceNode, flags: LoopFlags
) -> dict[str, Any]:
    return {
        "unit": decl.unit,
        "compose": flags.compose.value,
        "completion": flags.completion.value,
        "repeat": flags.repeat.value,
        "budget": _secs(decl.budget),
        "env_key_field": getattr(decl, "env_key_field", None),
        "declared_codes": [],
    }


def leaf_node(decl: LeafDeclaration) -> dict[str, Any]:
    node = _common(decl, decl.flags)
    node["declared_codes"] = sorted(set(decl.retryable) | {r.code for r in decl.remedies})
    node.update(
        preconditions=list(decl.preconditions),
        postcondition=decl.postcondition,
        wait={
            "poll_every": _secs(decl.wait.poll_every),
            "backoff": decl.wait.backoff,
            "max_wait": _secs(decl.wait.max_wait),
        },
        resource_kind=decl.resource_kind,
        may_touch=sorted(decl.may_touch),
        effects=[
            {
                "effect": e.effect,
                "facet": e.facet.value,
                "verb": e.verb,
                "lifetime": e.lifetime.value,
                "host_sections": sorted(s.value for s in e.host_sections),
                "release_timeout": _secs(e.release_timeout),
                "is_release": e.is_release,
            }
            for e in decl.effects
        ],
        retryable=sorted(decl.retryable),
        remedies=[
            {
                "code": r.code,
                "effect": r.effect,
                "attempts": r.attempts,
                "total": _secs(r.total),
                "cooldown": _secs(r.cooldown),
            }
            for r in decl.remedies
        ],
        max_attempts=decl.max_attempts,
    )
    return node


def all_node(decl: AllDeclaration) -> dict[str, Any]:
    node = _common(decl, decl.flags)
    node.update(
        children=[
            {
                "name": c.name if c.name is not None else c.unit,
                "binding": {
                    "unit": c.unit,
                    "params": dict(c.params),
                    "needs": list(c.needs),
                    "vantage": c.vantage.value,
                },
                "path": None,
            }
            for c in decl.children
        ],
        concurrency=decl.concurrency,
        gates=list(decl.gates),
        identifier_sets={name: sorted(members) for name, members in decl.identifier_sets.items()},
        arg_bindings=[
            {
                "arg": b.arg,
                "identifier_set": b.identifier_set,
                "filters_children": b.filters_children,
            }
            for b in decl.arg_bindings
        ],
    )
    return node


def choice_node(decl: ChoiceNode) -> dict[str, Any]:
    node = _common(decl, decl.flags)
    choice = decl.choice
    node["choice"] = {
        "logical_system": choice.logical_system,
        "alternatives": [
            {
                "unit": a.unit,
                "realization": a.realization.value,
                "reachable_from": sorted(v.value for v in a.reachable_from),
                "human_action": a.human_action,
                "path": None,
            }
            for a in choice.alternatives
        ],
        "select_arg": choice.select_arg,
        "fallback": choice.fallback,
        "readiness": choice.readiness,
    }
    return node


def declaration_node(decl: object) -> dict[str, Any]:
    """The wire node for one declaration (a leaf, an ALL composite or a CHOICE node)."""
    if isinstance(decl, LeafDeclaration):
        return leaf_node(decl)
    if isinstance(decl, AllDeclaration):
        return all_node(decl)
    if isinstance(decl, ChoiceNode):
        return choice_node(decl)
    raise ExtractionRefused(type(decl).__name__, "not a declaration")


def resolve_unit(entry: WorkflowEntry, name: str) -> LeafDeclaration | AllDeclaration | ChoiceNode:
    """One unit's declaration: `declare()` for a unit, the object itself for a composite."""
    units: Mapping[str, object] = entry.units
    if name not in units:
        raise ExtractionRefused(name, "unit is not in the entry's units")
    unit = units[name]
    if isinstance(unit, (LeafDeclaration, AllDeclaration, ChoiceNode)):
        return unit
    declare = getattr(unit, "declare", None)
    if not callable(declare):
        raise ExtractionRefused(name, "unit has no declare()")
    try:
        declared = declare()
    except Exception as exc:  # plugin code: any failure is a refusal, never a partial tree
        raise ExtractionRefused(name, f"declare() raised {type(exc).__name__}: {exc}") from exc
    if not isinstance(declared, (LeafDeclaration, AllDeclaration, ChoiceNode)):
        raise ExtractionRefused(name, f"declare() returned {type(declared).__name__}")
    return declared


def resolve_root(entry: WorkflowEntry) -> LeafDeclaration | AllDeclaration | ChoiceNode:
    """The root's declaration: `declare()` for a unit, the object itself for a composite."""
    if entry.root not in entry.units:
        raise ExtractionRefused(entry.root, "root unit is not in the entry's units")
    return resolve_unit(entry, entry.root)


def resolve_descendants(
    entry: WorkflowEntry, root: LeafDeclaration | AllDeclaration | ChoiceNode
) -> dict[str, dict[str, Any]]:
    """The declared tree's nodes: the root at `ROOT_PATH` and every descendant at its canonical
    path (V-1.2), each child reference carrying the path of the node it names. Every reachable
    unit is declared once (`declare()` runs once per logical node, however many aliases)."""
    nodes: dict[str, dict[str, Any]] = {}
    placed: dict[tuple[str, str, str], str] = {("node", entry.root, entry.root): ROOT_PATH}
    taken: set[str] = {ROOT_PATH}

    def assign(parent: str, name: str) -> str:
        base = f"{parent}/{name}" if parent != ROOT_PATH else name
        path, n = base, 2
        while path in taken:  # a second logical node under one name: distinct, so it conflicts
            path, n = f"{base}#{n}", n + 1
        taken.add(path)
        return path

    def visit(path: str, decl: LeafDeclaration | AllDeclaration | ChoiceNode) -> None:
        node = declaration_node(decl)
        nodes[path] = node
        if node["compose"] == "all":
            refs = [
                (c, c["name"], c["binding"]["unit"], ("node", c["binding"]["unit"], c["name"]))
                for c in node["children"]
            ]
        elif node["compose"] == "choice":
            refs = [
                (a, a["unit"], a["unit"], ("alt", a["unit"], path))
                for a in node["choice"]["alternatives"]
            ]
        else:
            return
        for ref, name, unit, key in refs:
            if unit not in entry.units:
                continue  # an unresolved name: `path` stays null, admission refuses it (L.TR-1.1)
            if key not in placed:
                child_path = assign(path, name)
                placed[key] = child_path
                visit(child_path, resolve_unit(entry, unit))
            ref["path"] = placed[key]

    visit(ROOT_PATH, root)
    return nodes


def extract_root(
    entry: WorkflowEntry,
) -> tuple[LeafDeclaration | AllDeclaration | ChoiceNode, DeclaredTree]:
    """The root's declaration and the declared tree built from that one value, or raise
    `ExtractionRefused`. The loop proves the admitted digest against the tree and then walks the
    very declaration the digest covers (B1-O3).

    The root declaration must pass `check_declaration` (B1-E1, L.SL-7.1) first: the refusal
    carries the first registration refusal's stable code and names its element."""
    decl = resolve_root(entry)
    refusals = check_declaration(decl)
    if refusals:
        first = refusals[0]
        extra = f" (+{len(refusals) - 1} more)" if len(refusals) > 1 else ""
        raise ExtractionRefused(entry.root, first.message + extra, code=first.code)
    try:
        return decl, DeclaredTree.build(entry.root, resolve_descendants(entry, decl))
    except DeclaredTreeInvalid as exc:
        raise ExtractionRefused(entry.root, f"declaration not encodable: {exc}") from exc


def extract_declared_tree(entry: WorkflowEntry) -> DeclaredTree:
    """Extract `entry`'s declared tree, or raise `ExtractionRefused` (through `extract_root`)."""
    return extract_root(entry)[1]
