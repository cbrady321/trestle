"""Test running for the workflow loop (L.RB-5.1; B3-C14, B3-C21, WR-VERIFY-4).

A test run is not a port of its own: `TestRun` is `ExecutionPort` (B3-C21, family "Provisioning &
Testing"). `PytestJunitRunner` is an `ExecutionPort` over another one (the real `CommandPort`) that
runs one pytest selector out of process and reads its result from a JUnit artifact. It imports only
the standard library and `trestle.workflow` (BFD-47, N7).
"""

from trestle_packs.testrun.pytest_junit import (
    OutputHandle,
    PytestJunitRunner,
    read_junit,
)

__all__ = ["OutputHandle", "PytestJunitRunner", "read_junit"]
