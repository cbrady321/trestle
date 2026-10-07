"""Reading a run's evidence/state.json (v0.3.1 Problem C).

The owner writes state.json after each ledger row that changes it (`ledger.STATE_KINDS`), the
ledger row first and state.json by atomic rename, so state.json can only lag the ledger, never lead
it. status(), the waiters and the run listings read it instead of parsing the ledger. A reader that
needs certainty and finds it non-terminal while the run's owner lock is free re-reads the ledger:
the owner may have died between its terminal row and state.json (the reaper rewrites it). A run
without state.json (admitted before v0.3.1) is read from its ledger, as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trestle.server.home import is_locked, owner_lock_path
from trestle.server.ledger import NON_TERMINAL_STATES, RunLedger, read_state, write_state


def trusted_state(run_dir: Path) -> dict[str, Any] | None:
    """The run's state.json when a reader that needs certainty may answer from it: it is terminal,
    or non-terminal while the owner lock is held. None means read the ledger: no state.json (an
    old run), or a non-terminal one whose owner lock is free."""
    state = read_state(run_dir)
    if state is None:
        return None
    if state["state"] in NON_TERMINAL_STATES and not is_locked(owner_lock_path(run_dir)):
        return None
    return state


def refresh_state(run_dir: Path, ledger: RunLedger) -> None:
    """Rewrite state.json from `ledger` when it lags the last row (an owner that died between a row
    and state.json). Only the run's lock holder calls this: the reaper, after its takeover."""
    if not ledger.records:
        return
    state = read_state(run_dir)
    last = ledger.records[-1].get("seq")
    if state is None or state.get("seq") != last:
        write_state(ledger.path.parent, ledger.records)
