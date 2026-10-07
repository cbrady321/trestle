"""T1's ledger check of J-TRL (L.TR-L.1; MC-B3-04): every host node of the lift set PASSED.

`check(ledger_path)` reads an explicit ledger of proof results, never the working tree's results
directory, and returns one line per problem: for each clause of `lift_set_trl.toml`, every listed
host node id must have a `passed` record for the clause (its `labels` carry the clause, so the
node carries `proves(row, clause, "A", "tree", tier, venue)`) in a named gate on 3.12 at venue
CI or HOST, and no record of that node may be `failed`, `skipped`, `xfailed`, `xpassed`,
`error` or `unrun` (MC-02: a node that did not pass renders its clause UNPROVEN). A node with no
record is missing.

The ledger file is JSON: a list of result records (`tests/proof/results.py::Record`, one per test
outcome, as the proof-ledger job reads them) or `{"records": [...]}`, or a `.jsonl` file of the
same records. The clause-keyed report `meta report --json` renders carries no node ids, so the
node-level check refuses it with a message saying so.

The module matches no default pytest pattern (its name does not begin `test_`), so the default
session never collects `test_lift_set_closed`: it runs only when named, at MJ.TR-L T1 over the
ledger of the PR head and at J-SLICE-A S5 (which sets `TRESTLE_LIFT_LEDGER` itself to the ledger it
rendered). Unset, `test_lift_set_closed` fails, never skips (DM-80)."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

LIFT_SET_FILE = Path(__file__).with_name("lift_set_trl.toml")
LEDGER_ENV = "TRESTLE_LIFT_LEDGER"
BAD_OUTCOMES = frozenset({"failed", "skipped", "xfailed", "xpassed", "error", "unrun"})
COUNTING_VENUES = ("CI", "HOST")


def load_lift_set(path: Path = LIFT_SET_FILE) -> dict[str, list[str]]:
    return {clause: list(nodes) for clause, nodes in tomllib.loads(path.read_text()).items()}


def load_records(ledger_path: Path) -> list[dict[str, Any]]:
    """The ledger's result records (see the module docstring for the accepted shapes)."""
    text = ledger_path.read_text(encoding="utf-8")
    if ledger_path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return list(data["records"])
    if isinstance(data, list):
        return data
    raise ValueError(
        f"{ledger_path}: not a list of result records (a clause-keyed report names no node ids)"
    )


def _counts(record: dict[str, Any]) -> bool:
    return (
        record.get("outcome") == "passed"
        and bool(record.get("gate"))
        and record.get("venue") in COUNTING_VENUES
        and str(record.get("interpreter", "")).startswith("3.12")
    )


def check(ledger_path: Path, lift_set: dict[str, list[str]] | None = None) -> list[str]:
    """One line per problem in `ledger_path` against the lift set; empty when it is closed."""
    lift_set = lift_set if lift_set is not None else load_lift_set()
    try:
        records = load_records(ledger_path)
    except (OSError, ValueError) as exc:
        return [f"cannot read the ledger: {exc}"]
    by_node: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_node.setdefault(str(record.get("nodeid")), []).append(record)
    problems: list[str] = []
    for clause, nodes in sorted(lift_set.items()):
        for node in nodes:
            found = by_node.get(node, [])
            if not found:
                problems.append(f"{clause}: host node {node} has no record in the ledger")
                continue
            bad = sorted({str(r.get("outcome")) for r in found} & BAD_OUTCOMES)
            if bad:
                problems.append(f"{clause}: host node {node} did not pass ({', '.join(bad)})")
            elif not any(_counts(r) and clause in (r.get("labels") or []) for r in found):
                problems.append(
                    f"{clause}: host node {node} has no passing 3.12 CI or HOST record "
                    f"carrying the clause"
                )
    return problems


def test_lift_set_closed() -> None:
    """T1: every lift-set host node passed in the ledger named by `TRESTLE_LIFT_LEDGER`. Fails,
    never skips, when the variable is unset."""
    named = os.environ.get(LEDGER_ENV)
    assert named, f"{LEDGER_ENV} is unset: name the ledger to check"
    problems = check(Path(named))
    assert not problems, "\n".join(problems)
