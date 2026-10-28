"""Proof-court-only fixture plugin (L.P0-0c.6): raises unconditionally, so a
run of it always reaches the ledger's terminal `failed` state.

Lives under `tests/proof/fixtures/plugins/`, never `tests/fixtures/plugins/`
(the S0-enumerated shared fixture directory) — the proof court's own fossil
generator passes this directory to `create_kernel` explicitly and never
touches the shared one, so S0's plugin catalog is untouched (P-S0, P-ID).
"""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def boom(ctx: Context) -> dict[str, str]:
    raise RuntimeError("boom: this plugin always fails (proof-court fossil fixture)")
