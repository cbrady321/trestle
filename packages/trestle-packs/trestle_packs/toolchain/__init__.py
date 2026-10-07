"""Toolchain resolution for the workflow loop's Toolchain Resolution & Provisioning family
(L.RB-4.2, L.RB-4.3; B3-C12, B3-C14, B3-C15). STUB-PROVEN against the mise-shaped stub
(OPEN-MISE-HOST, D-1).

`resolver` holds `MiseToolchainResolver` (`ToolchainResolver`: exact pin or `Unresolved`, never a
search-path fallback); `classify` holds the contract-violation classification of a task's outcome
(exit status, report and artifacts that disagree, B3-C14). The adapters import only the standard
library and `trestle.workflow` (BFD-47, C.5 step 4); `fakes/toolchain.py` is the stdlib-only fake
the same suite runs against.
"""

from trestle_packs.toolchain.classify import (
    Reported,
    bound_excerpt,
    classify,
    read_report,
    reproduction,
)
from trestle_packs.toolchain.resolver import (
    TOOLCHAIN_INTERFACE_DRIFT,
    TOOLCHAIN_MISSING,
    MiseToolchainResolver,
)

__all__ = [
    "TOOLCHAIN_INTERFACE_DRIFT",
    "TOOLCHAIN_MISSING",
    "MiseToolchainResolver",
    "Reported",
    "bound_excerpt",
    "classify",
    "read_report",
    "reproduction",
]
