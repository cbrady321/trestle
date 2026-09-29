"""Workflow declaration surface (L.SV-2.1). Plugins import public names from here; the
package imports only stdlib, `trestle.plugin` and `trestle.common.plan` (C.5 step 4)."""

from trestle.workflow.declarations import (
    AllDeclaration,
    Alternative,
    ArgBinding,
    ChildBinding,
    ChoiceDeclaration,
    ChoiceNode,
    CompletionSource,
    Compose,
    Declaration,
    EffectDeclaration,
    EffectFacetClass,
    HostScopeRef,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    RemedyDeclaration,
    Repeat,
    Vantage,
    WaitPolicy,
    WorkflowEntry,
)

# Submodules a plugin may import from (`import trestle.workflow.<m>`); every other module of the
# package, and every name that starts with an underscore, is refused at publication (L.SV-2.3).
PUBLIC_MODULES: tuple[str, ...] = ("declarations",)

__all__ = [
    "AllDeclaration",
    "Alternative",
    "ArgBinding",
    "ChildBinding",
    "ChoiceDeclaration",
    "ChoiceNode",
    "CompletionSource",
    "Compose",
    "Declaration",
    "EffectDeclaration",
    "EffectFacetClass",
    "HostScopeRef",
    "LeafDeclaration",
    "Lifetime",
    "LoopFlags",
    "RealizationKind",
    "RemedyDeclaration",
    "Repeat",
    "Vantage",
    "WaitPolicy",
    "WorkflowEntry",
]
