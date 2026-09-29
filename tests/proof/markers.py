"""Proof marker vocabulary (MC-01) and `target_check` (L.P0-0a.2).

Registered markers:
  proves(row, clause, slice, step, tier, venue) — this result proves a
    clause of a row (`slice` in {A, B, core}).
  pin(gap) — this test pins the current behavior of gap token `gap`.
  target(gap) — this test targets gap token `gap` for a future change.
  compat — this test is part of the WR-COMPAT preservation gate.
  stub_proven(label) — the clause is proven against a stub, not the real
    tool.
  gated_on(q) — this test's proof is gated on open question `q`.
  na(reason) — this clause is not applicable, for the stated reason.
"""

from __future__ import annotations

VALID_SLICES = ("A", "B", "core")

MARKER_DOCS = {
    "proves": (
        "proves(row, clause, slice, step, tier, venue): this test result "
        "proves clause 'clause' of row 'row' (slice in {A, B, core})"
    ),
    "pin": "pin(gap): this test pins the current behavior of gap token 'gap'",
    "target": "target(gap): this test targets gap token 'gap' for a future behavior change",
    "compat": "compat: this test is part of the WR-COMPAT preservation gate",
    "stub_proven": "stub_proven(label): this clause is proven against a stub, not the real tool",
    "gated_on": "gated_on(q): this test's proof is gated on open question 'q'",
    "na": "na(reason): this clause is not applicable, for the stated reason",
}


class TargetUnmet(AssertionError):
    """Raised by `target_check` when its condition does not hold.

    Carries the gap token so a failure names the gap it targets rather than
    just the assertion text.
    """

    def __init__(self, gap: str, detail: str) -> None:
        super().__init__(f"{gap}: {detail}")
        self.gap = gap
        self.detail = detail


def target_check(cond: bool, gap: str, detail: str) -> None:
    """Raise `TargetUnmet(gap, detail)` unless `cond` holds."""
    if not cond:
        raise TargetUnmet(gap, detail)
