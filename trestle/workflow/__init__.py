"""Workflow declaration surface (L.SV-2.1). Plugins import public names from here; the
package imports only stdlib, `trestle.plugin` and `trestle.common.plan` (C.5 step 4).

Besides the declaration data it re-exports the unit-author names of the public `units` and
`values` modules (the step and observation types a work unit returns and reads, B1-C1..C8), so
`from trestle.workflow import Acted, Observation` works as well as the module imports. `run_tree`
and the port protocols stay in their modules (`trestle.workflow.loop`, `trestle.workflow.ports`):
importing this package does not load the loop."""

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
from trestle.workflow.units import (
    ActContext,
    Acted,
    Blocked,
    EffectFacets,
    EffectRefused,
    Failed,
    NoAction,
    NodeRecordView,
    ObserveContext,
    ReadFacets,
    ReleaseFacets,
    Step,
    StepView,
    Ticketed,
    TicketView,
    WorkUnit,
)
from trestle.workflow.values import (
    CancelSignal,
    CheckResult,
    Condition,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    CurrencyFact,
    FoundRef,
    Lineage,
    NodePath,
    Observation,
    OwnedHandle,
    Provenance,
    RecordedResult,
    RemedyGrant,
    Resend,
    SelectorRef,
    Verdict,
    WaitOutcome,
)

# Submodules a plugin may import from (`import trestle.workflow.<m>`); every other module of the
# package, and every name that starts with an underscore, is refused at publication (L.SV-2.3).
# `declarations` is the declaration data (L.SV-2.1); `units`, `values`, `ports` and `loop` are what
# a workflow author writes a work unit against (B1-C1..C9: the unit contract types and the value
# types they carry, the port protocols, and `run_tree`, the plugin callable's one call), public
# from L.SV-5.9 because the loop and its ports exist from there. Importing `ports` is the D-b
# port import (`env_arg` required, WR-OWN-8); the rest of the package (`extract`, `services`,
# `facets`, `join`, `decide`, `codes`, `human_actions`) is the loop's own and stays private.
PUBLIC_MODULES: tuple[str, ...] = ("declarations", "loop", "ports", "units", "values")

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
    # units (B1-C1..C8)
    "ActContext",
    "Acted",
    "Blocked",
    "EffectFacets",
    "EffectRefused",
    "Failed",
    "NoAction",
    "NodeRecordView",
    "ObserveContext",
    "ReadFacets",
    "ReleaseFacets",
    "Step",
    "StepView",
    "TicketView",
    "Ticketed",
    "WorkUnit",
    # values (V-1..V-4)
    "CancelSignal",
    "CheckResult",
    "Condition",
    "Confirmation",
    "ConfirmationStatus",
    "CreatedHandle",
    "CurrencyFact",
    "FoundRef",
    "Lineage",
    "NodePath",
    "Observation",
    "OwnedHandle",
    "Provenance",
    "RecordedResult",
    "RemedyGrant",
    "Resend",
    "SelectorRef",
    "Verdict",
    "WaitOutcome",
]
