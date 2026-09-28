"""Derived proof ledger renderer (MC-02) (L.P0-0a.3).

A clause/label is PROVEN only when it has >=1 registered node and every
registered node passed in a named gate on 3.12 (CI) or in a valid HOST
record (`venue in {CI, HOST}`, `gate` declared). xfail/skip/error/unrun
render UNPROVEN. A 3.14-only pass is corroborating, never PROVEN by itself.
`venue = LOCAL` never counts, at all. An undeclared gate's results are
ignored entirely (as if they were never recorded).
"""

from __future__ import annotations

from pathlib import Path

from tests.proof import results as results_mod

PROVEN = "PROVEN"
UNPROVEN = "UNPROVEN"

_BAD_OUTCOMES = {"skipped", "xfailed", "xpassed", "error", "unrun", "failed"}


class VacuousLedgerError(RuntimeError):
    """Raised by `render()` when there are zero countable results at all."""


def _counts_gate(record: dict[str, object], declared: set[str]) -> bool:
    gate = record.get("gate")
    return bool(gate) and gate in declared


def render(
    results_dir: Path | None = None,
    meta_config_path: Path | None = None,
    gates_dir: Path | None = None,
) -> dict[str, dict[str, object]]:
    # Resolved at call time (not as bound default parameter values) so a
    # test can `monkeypatch.setattr(results_mod, "RESULTS_DIR", ...)` and
    # have a bare `render()` call pick it up.
    if results_dir is None:
        results_dir = results_mod.RESULTS_DIR
    if meta_config_path is None:
        meta_config_path = results_mod.META_CONFIG_PATH
    if gates_dir is None:
        gates_dir = results_mod.GATES_DIR
    declared = results_mod.declared_gates(meta_config_path, gates_dir)
    records = results_mod.read_records(results_dir)

    countable = [
        r
        for r in records
        if r.get("venue") != results_mod.LOCAL_VENUE and _counts_gate(r, declared)
    ]
    if not countable:
        raise VacuousLedgerError("0 countable results: every clause would render vacuously")

    by_clause: dict[str, list[dict[str, object]]] = {}
    for rec in countable:
        for label in rec.get("labels", []) or []:
            by_clause.setdefault(label, []).append(rec)

    report: dict[str, dict[str, object]] = {}
    for clause, recs in by_clause.items():
        any_312_pass_named_gate = any(
            r["outcome"] == "passed"
            and str(r["interpreter"]).startswith("3.12")
            and r["venue"] in ("CI", "HOST")
            for r in recs
        )
        any_bad = any(r["outcome"] in _BAD_OUTCOMES for r in recs)
        any_314_pass = any(
            r["outcome"] == "passed" and str(r["interpreter"]).startswith("3.14") for r in recs
        )
        status = PROVEN if (any_312_pass_named_gate and not any_bad) else UNPROVEN
        report[clause] = {
            "status": status,
            "corroborating_314": any_314_pass,
            "n_results": len(recs),
        }
    return report
