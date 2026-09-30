"""Stdlib-only fakes of the B3 ports the workflow loop calls (L.SV-5.16; MC-25, SA-14).

`FakeCommand` (an `ExecutionPort`) and `FakeMarker` (`ResourceReads`, `ResourceCreate` and
`ResourceOwned` over a marker that is a fake in-run process or a plain file). They import nothing
but the standard library: until BFD-47 lands at SL-3 a pack does not import `trestle.workflow`, so
the values they return carry the field names of the B3 types they stand for, and a release
descriptor is its wire mapping. The same conformance suite runs against them as against the real
adapters (`tests/proof/suites/ports`, `[fake-command]`, `[fake-marker]`).
"""

from trestle_packs.fakes.command import (
    BoundCommand,
    Confirmation,
    ConfirmationStatus,
    ExecutionClass,
    ExecutionPolicy,
    ExecutionResult,
    FakeCommand,
    Helpers,
    RecordedResult,
    Resolved,
    SelfProvisioning,
    TestCounts,
    durable,
    failed_result,
    in_run_group,
    passed_result,
)
from trestle_packs.fakes.marker import (
    CheckResult,
    Endpoint,
    FakeMarker,
    FoundRef,
    ResourceObservation,
    RouteRefused,
    SelectorRef,
    run_scoped_selector,
)

__all__ = [
    "BoundCommand",
    "CheckResult",
    "Confirmation",
    "ConfirmationStatus",
    "Endpoint",
    "ExecutionClass",
    "ExecutionPolicy",
    "ExecutionResult",
    "FakeCommand",
    "FakeMarker",
    "FoundRef",
    "Helpers",
    "RecordedResult",
    "Resolved",
    "ResourceObservation",
    "RouteRefused",
    "SelectorRef",
    "SelfProvisioning",
    "TestCounts",
    "durable",
    "failed_result",
    "in_run_group",
    "passed_result",
    "run_scoped_selector",
]
