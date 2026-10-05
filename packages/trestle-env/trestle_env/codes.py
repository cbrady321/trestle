"""The environment half of the Slice B code set (MC-B-12; L.RB-0.6).

`environment.<snake>` codes the reference workflow itself declares for meanings V-11 does not
name (V-11: a code outside its table, a boundary's error rows or a workflow's declared codes is a
defect). Stdlib only, and not the library vocabulary: the adapter half lives in
`trestle.common.plan.vocabulary` and this package may not import it (root C.5 step 4).

Every other environment condition uses its V-11 code as the boundary raises it, and B defines none
of them: an unknown catalog identifier is admission's `UNKNOWN_IDENTIFIER`, a duplicate one
`admission.invalid_args`, an unsupported route `ROUTE_UNSUPPORTED`, a never-ready service
`POSTCONDITION_TIMEOUT`, a foreign occupant `FOUND_INCOMPATIBLE`, an unhealthy found resource
`FOUND_UNHEALTHY`, an older credential generation `CREDENTIAL_STALE` and a short lifetime
`CREDENTIAL_LIFETIME_INSUFFICIENT`.

The constant's NAME is the upper-cased origin-qualified snake (`environment.repository_missing` is
`ENVIRONMENT_REPOSITORY_MISSING`); a class is never attached here: B4-T2 decides it from the
`NodeEnd` the code rides (a declared code ends `BLOCKED` through row 9).
"""

from __future__ import annotations

ENVIRONMENT_REPOSITORY_MISSING = "environment.repository_missing"
"""A unit-authored `Blocked.code` (<= CODE_MAX, V-13): an override whose repository is absent."""

REFERENCE_WORKFLOW_CODES: frozenset[str] = frozenset({ENVIRONMENT_REPOSITORY_MISSING})
"""Every code the reference tree declares (the closed environment half of MC-B-12)."""
