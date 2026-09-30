"""V-11.1: human action and re-send per Trestle-produced code (L.SV-5.4).

The join fills these, and so will a facet or the loop for a code it puts on a node itself. Templates
may use `{path}`, `{effect}` and `{subject}`; a rendered text is at most `HUMAN_ACTION_MAX` by
construction (V-11.1). Only the rows the join, the facets and the loop's first leaves produce are
here; a code without a row takes V-11.1's last row.
"""

from __future__ import annotations

from typing import Final

from trestle.workflow import codes
from trestle.workflow.values import Resend

_AFTER = Resend.SUCCEEDS_AFTER_ACTION

TEMPLATES: Final[dict[str, tuple[str, Resend]]] = {
    codes.CREDENTIAL_INTERACTIVE: (
        "Authenticate identity {subject} interactively with its issuer, then re-send.",
        _AFTER,
    ),
    codes.TOOLCHAIN_MISSING: (
        "Install the pinned tool {subject} that {path} needs (Trestle installs none, OQ-18), "
        "then re-send.",
        _AFTER,
    ),
    codes.PRECONDITION_UNSATISFIED: (
        "Make the declared precondition of {path} hold, then re-send.",
        _AFTER,
    ),
    codes.CURRENCY_UNCONFIRMED: (
        "Refresh the consumer at {path} so it reports the host's current {subject}, then re-send.",
        _AFTER,
    ),
    codes.CREDENTIAL_LIFETIME_INSUFFICIENT: (
        "Extend the deadline, or provision a longer-lived {subject}: the issuer's lifetime does "
        "not cover the admitted deadline. Then re-send.",
        _AFTER,
    ),
    codes.FOUND_UNHEALTHY: (
        "Stop or repair the pre-existing, unready resource at {path} (Trestle never changes what "
        "it did not create), then re-send.",
        _AFTER,
    ),
    codes.FOUND_INCOMPATIBLE: (
        "Stop or reconfigure the pre-existing resource at {path}, whose identity or "
        "configuration is not the declared one, then re-send.",
        _AFTER,
    ),
    codes.CREDENTIAL_STALE: (
        "Refresh {subject} inside the pre-existing consumer at {path}, then re-send.",
        _AFTER,
    ),
    codes.EFFECT_UNCONFIRMED: (
        "Check whether {effect} of {path} took effect, and keep or undo it, before re-sending.",
        Resend.UNKNOWN,
    ),
    codes.REMEDY_EXHAUSTED: (
        "Diagnose {path} from its evidence: its declared repair was spent without success.",
        Resend.UNKNOWN,
    ),
    codes.REMEDY_NO_PROGRESS: (
        "Diagnose {path} from its evidence: the fault its repair targets persisted after the "
        "repair.",
        Resend.UNKNOWN,
    ),
    codes.LANE_UNAVAILABLE: (
        "Check the run directory's disk space and permissions: the attempt record for {path} "
        "could not be written.",
        Resend.UNKNOWN,
    ),
    codes.POSTCONDITION_TIMEOUT: (
        "Diagnose why {path} did not become ready within its wait, from its evidence.",
        Resend.UNKNOWN,
    ),
}

DEFAULT: Final[tuple[str, Resend]] = (
    "Diagnose {path} from its evidence before re-sending.",
    Resend.UNKNOWN,
)


def render(code: str | None, *, path: str, effect: str, subject: str) -> tuple[str, Resend]:
    """The human action and re-send V-11.1 gives `code`, its placeholders filled."""
    template, resend = TEMPLATES.get(code, DEFAULT) if code is not None else DEFAULT
    return template.format(path=path, effect=effect, subject=subject), resend
