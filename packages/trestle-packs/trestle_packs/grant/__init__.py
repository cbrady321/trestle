"""Demo credential grant adapters for the workflow loop's Demo Credential Serving family
(L.RB-9.1, L.RB-9.2; B3-C8, B3-C9, B3-C11, D-9: AWS is DEMO ONLY, never real).

`demo` holds `DemoGrant` (`GrantReads` + `GrantRefresh`) over the stub issuer on loopback. The
adapters import only the standard library and `trestle.workflow` (BFD-47, C.5 step 4); the legacy
subpackages are untouched. `fakes/grant.py` is the stdlib-only fake the same suite runs against.
"""

from trestle_packs.grant.demo import (
    CREDENTIAL_INTERACTIVE,
    CREDENTIAL_STALE,
    DEMO_CREDENTIAL_KIND,
    GRANT_ISSUER_UNREACHABLE,
    ConsumerProbe,
    DemoGrant,
    IssuerClient,
    ProbeReading,
)

__all__ = [
    "CREDENTIAL_INTERACTIVE",
    "CREDENTIAL_STALE",
    "DEMO_CREDENTIAL_KIND",
    "GRANT_ISSUER_UNREACHABLE",
    "ConsumerProbe",
    "DemoGrant",
    "IssuerClient",
    "ProbeReading",
]
