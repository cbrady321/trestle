"""`python -m tests.proof.differ d5 [--fossils <dir>]` (MC-06 mode d5; CSC-5 builder L.SV-4.2).

One key in one place (B4-I4): `project` recomputed from the same durable inputs equals the
finalized answer, and every child view equals the root answer's account of its vertex (B4-C1,
B4-C8; one vertex in A-1). Over:

* every committed fossil run that reached a terminal state: the answer is recomputed twice from the
  durable inputs (the same bytes each time), its outcome equals core's classification of the run
  (B4-T4: a plan-less root answers as `outcome.classify` does), every vertex of the run has an
  account in it, and where the run persisted `evidence/answer.json` the recomputation equals it;
* a live set produced now (a succeeded and a failed plain plugin): the answer U2 persisted at
  finalization equals the recomputation at read time, byte for byte.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from tests.proof import fossils as fossils_mod
from tests.proof import harness, tolerances
from trestle.common.outcome import classify
from trestle.server import answer
from trestle.server.ledger import TERMINAL_KINDS, RunLedger, evidence_dir, ledger_path

FIXTURE_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "plugins"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _recompute(run_dir: Path) -> tuple[answer.TerminalAnswer, dict[str, Any], str] | None:
    ledger = RunLedger.open(ledger_path(run_dir))
    state = ledger.projected_state()
    if state not in TERMINAL_KINDS:
        return None
    spec_path = evidence_dir(run_dir) / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.is_file() else {}
    ans = answer.answer_for_run(run_dir, ledger.records, state, spec)
    return ans, spec, state


def check_run(run_dir: Path) -> list[str]:
    """The differences for one terminal run directory (empty: none, or not terminal)."""
    result = _recompute(run_dir)
    if result is None:
        return []
    ans, spec, state = result
    diffs: list[str] = []
    again = _recompute(run_dir)
    assert again is not None
    if _canonical(answer.to_wire_full(ans)) != _canonical(answer.to_wire_full(again[0])):
        diffs.append("the answer is not the same bytes when recomputed")
    ledger = RunLedger.open(ledger_path(run_dir))
    if "plan" not in spec or not (spec.get("plan") or {}).get("declaration_digest"):
        klass = classify(state, ledger.last_kind("error_record"), recovered=state == "interrupted")
        if ans.outcome.value != klass.outcome_class.value:
            got, want = ans.outcome.value, klass.outcome_class.value
            diffs.append(f"plan-less outcome {got!r} differs from classify {want!r}")
    if answer.account_of(ans, ()) is None:
        diffs.append("the root vertex has no account in the answer (child view)")
    persisted = evidence_dir(run_dir) / answer.ANSWER_FILE
    if persisted.is_file():
        finalized = json.loads(persisted.read_text(encoding="utf-8"))
        if _canonical(finalized) != _canonical(answer.to_wire_full(ans)):
            diffs.append("the finalized answer differs from project recomputed at read time")
    return diffs


def _live_runs() -> list[Path]:
    with tempfile.TemporaryDirectory(prefix="d5-live-") as tmp:
        kernel = harness.fresh_kernel(
            plugin_dirs=[harness.DEFAULT_PLUGIN_DIR, FIXTURE_PLUGIN_DIR], home=Path(tmp) / "home"
        )
        dirs = [
            harness.run_to_dir(
                kernel, "echo", {"message": "d5"}, wait_ms=tolerances.HARNESS_WAIT_MS
            ),
            harness.run_to_dir(kernel, "boom", {}, wait_ms=tolerances.HARNESS_WAIT_MS),
        ]
        diffs: list[Path] = []
        for run_dir in dirs:
            found = check_run(run_dir)
            for diff in found:
                print(f"d5: DIFF: live/{run_dir.name}: {diff}")
            if not (evidence_dir(run_dir) / answer.ANSWER_FILE).is_file():
                print(f"d5: DIFF: live/{run_dir.name}: no finalized answer was persisted")
                diffs.append(run_dir)
            elif found:
                diffs.append(run_dir)
        return diffs


def run(fossils_root: Path) -> int:
    states = fossils_mod.load_states(fossils_root)
    checked = 0
    failures = 0
    for state_id, (band, entry) in sorted(states.items()):
        if entry.get("producer") in (None, "pending") or entry.get("absent"):
            continue
        home = fossils_root / band / state_id / "home"
        for run_dir in sorted((home / "runs").rglob("r_*")):
            if not run_dir.is_dir() or not ledger_path(run_dir).is_file():
                continue
            diffs = check_run(run_dir)
            checked += 1
            for diff in diffs:
                failures += 1
                print(f"d5: DIFF: {band}/{state_id}/{run_dir.name}: {diff}")
    failures += len(_live_runs())
    print(f"d5: {checked} fossil runs checked, live set checked, {failures} diffs")
    return 1 if failures else 0


def main(args: argparse.Namespace) -> int:
    root = Path(args.fossils) if args.fossils else fossils_mod.FOSSILS_ROOT
    return run(root)
