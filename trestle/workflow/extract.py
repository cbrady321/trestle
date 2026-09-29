"""Declaration extraction (L.SV-2.2): walk a `WorkflowEntry` and produce the `DeclaredTree`.

Runs in the publication throwaway validator, the only place plugin code is imported at publish
time. A leaf root extracts to one node; a composite root is recorded with unresolved children
(`path: null`). Resolving descendants is the tree phase's (TR-0) and changes no format.
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


def resolve_root(entry: WorkflowEntry) -> LeafDeclaration | AllDeclaration | ChoiceNode:
    """The root's declaration: `declare()` for a unit, the object itself for a composite."""
    units: Mapping[str, object] = entry.units
    if entry.root not in units:
        raise ExtractionRefused(entry.root, "root unit is not in the entry's units")
    unit = units[entry.root]
    if isinstance(unit, (LeafDeclaration, AllDeclaration, ChoiceNode)):
        return unit
    declare = getattr(unit, "declare", None)
    if not callable(declare):
        raise ExtractionRefused(entry.root, "root unit has no declare()")
    try:
        declared = declare()
    except Exception as exc:  # plugin code: any failure is a refusal, never a partial tree
        raise ExtractionRefused(
            entry.root, f"declare() raised {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(declared, (LeafDeclaration, AllDeclaration, ChoiceNode)):
        raise ExtractionRefused(entry.root, f"declare() returned {type(declared).__name__}")
    return declared


def extract_declared_tree(entry: WorkflowEntry) -> DeclaredTree:
    """Extract `entry`'s declared tree, or raise `ExtractionRefused`."""
    decl = resolve_root(entry)
    try:
        return DeclaredTree.build(entry.root, {ROOT_PATH: declaration_node(decl)})
    except DeclaredTreeInvalid as exc:
        raise ExtractionRefused(entry.root, f"declaration not encodable: {exc}") from exc
