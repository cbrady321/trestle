"""The checkpoint meta-check framework (CM-5, MC-28; L.P0-0d.4).

A condition module `tests/proof/ckpt/<name>.py` declares `TRIGGER_MERGE`
(the merge id whose `WR-Merge` trailer triggers evaluation — `"J-<NAME>"`,
or `"J0"`/`"J-ROOT"` for the two untagged checkpoints), `TAG` (`None` for
the two check-run checkpoints, else `"wr-ckpt/<name>"`), and `CONDITIONS`
(a list of `Condition`). Condition modules and their helpers never match
pytest's default collection patterns (`test_*.py`/`*_test.py`, DM-80).
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class UnknownCkptError(ValueError):
    pass


@dataclass
class Condition:
    id: str
    check: Callable[[str], tuple[bool, str]]
    merge_only: bool = False  # needs the merge commit itself; skipped by --preview


@dataclass
class ConditionResult:
    id: str
    ok: bool
    reason: str


def load_module(name: str):
    module_name = name.replace("-", "_")
    try:
        return importlib.import_module(f"tests.proof.ckpt.{module_name}")
    except ModuleNotFoundError as exc:
        raise UnknownCkptError(name) from exc


def evaluate(module, commit: str, preview: bool = False) -> list[ConditionResult]:
    results = []
    for cond in module.CONDITIONS:
        if preview and cond.merge_only:
            continue
        ok, reason = cond.check(commit)
        results.append(ConditionResult(cond.id, ok, reason))
    return results


def already_evaluated(
    module, commit_sha: str, cwd: Path | None = None, check_run_reader=None
) -> bool:
    """CM-5: the `ckpt` job is idempotent. If `TAG` already points at the
    evaluated commit, or a `ckpt` check run on that sha already concluded
    `success`, evaluating again is a no-op success."""
    from tests.proof import fence as fence_mod

    cwd = cwd or ROOT
    if module.TAG is not None:
        tags = fence_mod._git(cwd, "tag", "--points-at", commit_sha).stdout.split()  # noqa: SLF001
        return module.TAG in tags
    reader = check_run_reader or (lambda _sha: "missing")
    return reader(commit_sha) == "success"


def ledger_digest(report: dict | None = None) -> str:
    """The sha256 of the canonical (sorted-key) proof ledger."""
    if report is None:
        from tests.proof import ledger as ledger_mod

        try:
            report = ledger_mod.render()
        except ledger_mod.VacuousLedgerError:
            report = {}
    canonical = json.dumps(report, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
