"""WR-COMPAT-8 (L.P0-1A.7): the run-state vocabulary is preserved.

The S0 vocabulary is queued, running, succeeded, failed, cancelled,
timed_out, worker_exit and interrupted, with `crashed` a documented ledger
terminal kind that no S0 code path ever writes (K-13). Every committed S0
fossil must project into that vocabulary, and the terminal kinds the fossils
exhibit must be exactly the S0 set minus `crashed`.
"""

from __future__ import annotations

import pytest

from tests.pins.a_lifecycle import fossil_producers as fp
from tests.proof import records
from trestle.server.ledger import TERMINAL_KINDS, RunLedger, ledger_path

NON_TERMINAL_STATES = {"queued", "running"}
S0_TERMINAL_STATES = {"succeeded", "failed", "cancelled", "timed_out", "worker_exit", "interrupted"}
DOCUMENTED_ONLY = {"crashed"}
VOCABULARY = NON_TERMINAL_STATES | S0_TERMINAL_STATES


@pytest.mark.compat
@pytest.mark.proves("WR-COMPAT-8", "WR-COMPAT-8:preserved", "core", "core", "PROC", "CI")
def test_state_vocabulary_preserved() -> None:
    assert TERMINAL_KINDS == S0_TERMINAL_STATES | DOCUMENTED_ONLY

    projected: set[str] = set()
    terminals: set[str] = set()
    for run_dir in sorted((fp.FOSSILS_ROOT / fp.BAND).glob("*/home/runs/*/r_*")):
        projected.add(RunLedger.open(ledger_path(run_dir)).projected_state())
        terminal = records.node_record(run_dir).terminal
        if terminal is not None:
            terminals.add(terminal)

    assert projected <= VOCABULARY, projected - VOCABULARY
    assert projected == VOCABULARY, f"fossils do not exercise {VOCABULARY - projected}"
    assert terminals == S0_TERMINAL_STATES, terminals
    assert not (terminals & DOCUMENTED_ONLY), "no S0 fossil may record `crashed` (K-13)"
