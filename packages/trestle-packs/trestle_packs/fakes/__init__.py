"""Stdlib-only fakes of the B3 ports the workflow loop calls (L.SV-5.16; MC-25, SA-14).

`FakeCommand` (an `ExecutionPort`), `FakeMarker` (`ResourceReads`, `ResourceCreate` and
`ResourceOwned` over a marker that is a fake in-run process or a plain file) and `FakeLocalProcess`
(the same three protocols over a simulated process table, ruled like the real local-process port).
They import nothing but the standard library (they were written before BFD-47 let a pack import
`trestle.workflow`, and stay so a fake needs no `trestle` install), so the values they return
carry the field names of the B3 types they stand for, and a release descriptor is its wire
mapping. The same conformance suite runs against them as against the real adapters
(`tests/proof/suites/ports`, `[fake-command]`, `[fake-marker]`, `[fake-local]`).
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
from trestle_packs.fakes.local_process import FakeLocalProcess
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
    "FakeLocalProcess",
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
