"""Stable codes the join, the unit contract and the loop put on a node (V-11; L.SV-5.3).

Each is spelled as the snake of its V-11 name under its origin: node condition codes are
`execution.*` (they reach `error_record`; plan N4, DM-16). `trestle.common.plan.vocabulary`
(L.SV-4.1) is the host-side owner of the same spellings for everything the answer reads; this
module carries only the ones `trestle.workflow` itself compares or emits, so the package needs no
import from the host side. The SA-03 drift check reads both and requires them to agree.
"""

from __future__ import annotations

from typing import Final

EFFECT_UNCONFIRMED: Final = "execution.effect_unconfirmed"
PRECONDITION_UNSATISFIED: Final = "execution.precondition_unsatisfied"
FOUND_INCOMPATIBLE: Final = "execution.found_incompatible"
FOUND_UNHEALTHY: Final = "execution.found_unhealthy"
CURRENCY_UNCONFIRMED: Final = "execution.currency_unconfirmed"
POSTCONDITION_TIMEOUT: Final = "execution.postcondition_timeout"
CREDENTIAL_LIFETIME_INSUFFICIENT: Final = "execution.credential_lifetime_insufficient"
CREDENTIAL_INTERACTIVE: Final = "execution.credential_interactive"
CREDENTIAL_STALE: Final = "execution.credential_stale"
TOOLCHAIN_MISSING: Final = "execution.toolchain_missing"
REMEDY_EXHAUSTED: Final = "execution.remedy_exhausted"
REMEDY_NO_PROGRESS: Final = "execution.remedy_no_progress"
TICKET_REFUSED: Final = "execution.ticket_refused"
LANE_UNAVAILABLE: Final = "execution.lane_unavailable"
UNIT_RAISED: Final = "execution.unit_raised"
STOP_SEEN: Final = "execution.stop_seen"
DECLARATION_STALE: Final = "execution.declaration_stale"
# one spelling for admission and for the in-run dispatch re-check (V-11 `BUDGET_DOES_NOT_FIT`)
BUDGET_DOES_NOT_FIT: Final = "admission.budget_does_not_fit"

# V-11.2: the human-actionable NOT_APPLIED set (read by J-5a).
HUMAN_ACTIONABLE: Final = frozenset({CREDENTIAL_INTERACTIVE, TOOLCHAIN_MISSING})

# V-3.7 presence rule: these two codes carry a human action and a re-send on any condition.
PRESENCE_CODES: Final = frozenset({POSTCONDITION_TIMEOUT, EFFECT_UNCONFIRMED})
