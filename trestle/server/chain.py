"""Run chaining (v0.4 Feature 3): `run(after={...})`.

A run sent with `after` is admitted at once into the held state, where it takes no slot and spends
none of its deadline. Its owner's 250 ms pass reads the earlier run's `state.json` (and its
`result.json` for `match`) and either releases the run to its server's queue or ends it cancelled,
`execution.after_unmet`. This module holds the pure parts: the argument's shape, its identity
(canonical JSON, compared on a key join beside `args_hash`), its resolution to an earlier run, the
`hold_until` bound and the release decision.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trestle.common import clock
from trestle.server.idempotency import read_entries
from trestle.server.ledger import NON_TERMINAL_STATES, RunLedger, evidence_dir, ledger_path
from trestle.server.recovery import find_run_dir
from trestle.server.runstate import trusted_state

WHEN_SUCCEEDED = "succeeded"
WHEN_ENDED = "ended"
WHEN_VALUES = (WHEN_SUCCEEDED, WHEN_ENDED)
AFTER_FIELDS = frozenset({"run", "key", "when", "match"})
# twice the reaper's 10 s period: one full pass and the reaping itself fit (Feature 3, Rows)
HOLD_SLACK_S = 20.0

# why a held run ended unmet (the `reason` of `execution.after_unmet`; the message names the run)
REASON_EARLIER_NOT_SUCCEEDED = "earlier_not_succeeded"
REASON_MATCH_MISMATCH = "match_mismatch"
REASON_RESULT_ABSENT = "result_absent"
REASON_RESULT_TOO_LARGE = "result_too_large"
REASON_RESULT_INVALID = "result_invalid"
REASON_EARLIER_MISSING = "earlier_missing"
REASON_HOLD_EXPIRED = "hold_expired"
# why a held run was released
REASON_MET = "after_met"


@dataclass(frozen=True)
class After:
    """A validated `after` argument."""

    run: str | None
    key: str | None
    when: str = WHEN_SUCCEEDED
    match: dict[str, Any] | None = None

    def canonical(self) -> str:
        """The call's identity in a key's file and `created`: the argument as the caller gave it,
        with the default `when` filled in, as canonical JSON."""
        body: dict[str, Any] = {"when": self.when}
        if self.run is not None:
            body["run"] = self.run
        if self.key is not None:
            body["key"] = self.key
        if self.match is not None:
            body["match"] = self.match
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_after(raw: object) -> After | str:
    """`raw` as an `After`, or the text of why it is not one (`admission.invalid_args`): exactly one
    of `run` and `key`, `when` succeeded or ended, `match` an object of top-level result fields and
    refused with `when: ended`."""
    if not isinstance(raw, dict):
        return "after must be an object"
    unknown = sorted(str(name) for name in raw if name not in AFTER_FIELDS)
    if unknown:
        return f"after has unknown fields: {', '.join(unknown)}"
    run, key = raw.get("run"), raw.get("key")
    if (run is None) == (key is None):
        return "after needs exactly one of run and key"
    target = run if run is not None else key
    if not isinstance(target, str) or not target:
        return "after.run and after.key must be non-empty strings"
    when = raw.get("when", WHEN_SUCCEEDED)
    if when not in WHEN_VALUES:
        return f"after.when must be one of {', '.join(WHEN_VALUES)}"
    match = raw.get("match")
    if "match" in raw and match is not None:
        if not isinstance(match, dict):
            return "after.match must be an object of top-level result fields"
        if when == WHEN_ENDED:
            return "after.match applies only with when: succeeded"
        try:
            json.dumps(match, sort_keys=True)
        except (TypeError, ValueError):
            return "after.match must be JSON"
    return After(
        run=run,
        key=key,
        when=str(when),
        match=match if isinstance(match, dict) else None,
    )


def resolve(home: Path, after: After) -> tuple[str, Path] | None:
    """The earlier run `after` names and its directory: `run` as given, a `key` its newest run
    whose directory exists, expired or not (the lookup `await_runs(keys=)` makes). None: no such
    run (`admission.after_unknown`). The caller holds the admission lock."""
    if after.run is not None:
        run_dir = find_run_dir(home, after.run)
        return (after.run, run_dir) if run_dir is not None else None
    assert after.key is not None
    for entry in read_entries(home, after.key):
        run_dir = find_run_dir(home, entry.run_id)
        if run_dir is not None:
            return entry.run_id, run_dir
    return None


def hold_until(earlier_dir: Path, *, now: float | None = None) -> float:
    """The latest moment the earlier run can have ended, epoch seconds: its deadline plus the
    finalization margin its own `created` row recorded plus the reaper's slack; when it is itself
    held, its `hold_until` plus its `deadline_s` first. Never before `now`, so a window that starts
    here never starts before the admission."""
    at = time.time() if now is None else now
    ledger = RunLedger.open(ledger_path(earlier_dir))
    created = ledger.last_kind("created") or {}
    margin = _number(created.get("finalization_margin_s"), float(clock.finalization_margin))
    if ledger.projected_state() == "held":
        deadline = _number(created.get("hold_until"), at) + _number(created.get("deadline_s"), 0.0)
    else:
        deadline = _deadline_epoch(earlier_dir, ledger, at)
    return max(deadline + margin + HOLD_SLACK_S, at)


def _deadline_epoch(run_dir: Path, ledger: RunLedger, default: float) -> float:
    raw = ledger.released_deadline()
    if raw is None:
        try:
            spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
            raw = spec.get("deadline") if isinstance(spec, dict) else None
        except (OSError, ValueError):
            raw = None
    try:
        fixed = datetime.fromisoformat(str(raw))
    except ValueError:
        return default
    if fixed.tzinfo is None:
        fixed = fixed.replace(tzinfo=UTC)
    return fixed.timestamp()


def _number(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default


@dataclass(frozen=True)
class HeldVerdict:
    """The pass's decision on a held run: released, or unmet for `reason` (`message` names the
    earlier run)."""

    release: bool
    reason: str
    message: str = ""


RELEASE = HeldVerdict(release=True, reason=REASON_MET)


def evaluate(home: Path, held_dir: Path, *, now: float | None = None) -> HeldVerdict | None:
    """Rule 7's release check for one held run, read-only: None keeps it held. The earlier run's
    state comes from its `state.json` (its ledger when that is not to be trusted, `runstate`) and
    `match` from its `result.json`. A hold past `hold_until` with the earlier run still not ended
    is unmet (`hold_expired`): the reaper should have finalized it by then."""
    at = time.time() if now is None else now
    created = RunLedger.open(ledger_path(held_dir)).last_kind("created") or {}
    after = _stored_after(created.get("after"))
    earlier = created.get("after_run_id")
    if after is None or not isinstance(earlier, str):
        return _unmet(REASON_EARLIER_MISSING, "the run's after is not readable")
    earlier_dir = find_run_dir(home, earlier)
    if earlier_dir is None:
        return _unmet(REASON_EARLIER_MISSING, f"run {earlier} no longer exists")
    state_row = trusted_state(earlier_dir)
    if state_row is not None:
        state = str(state_row["state"])
    else:
        state = RunLedger.open(ledger_path(earlier_dir)).projected_state()
    if state in NON_TERMINAL_STATES:
        if at > _number(created.get("hold_until"), float("inf")):
            return _unmet(REASON_HOLD_EXPIRED, f"run {earlier} had not ended by hold_until")
        return None
    if after.when == WHEN_SUCCEEDED:
        if state != "succeeded":
            return _unmet(REASON_EARLIER_NOT_SUCCEEDED, f"run {earlier} ended {state}")
        if after.match is not None:
            return _match(earlier, earlier_dir, after.match)
    return RELEASE


def _stored_after(raw: object) -> After | None:
    """`created.after` (canonical JSON) as an `After`."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    result = parse_after(parsed)
    return result if isinstance(result, After) else None


def _unmet(reason: str, message: str) -> HeldVerdict:
    return HeldVerdict(release=False, reason=reason, message=f"{message} ({reason})")


def _match(earlier: str, earlier_dir: Path, match: dict[str, Any]) -> HeldVerdict:
    """`match`: equality on top-level fields of the earlier run's `result.json`. A result that is
    absent, too large to keep or not a JSON object is unmet, each with its own reason."""
    evidence = evidence_dir(earlier_dir)
    result_path = evidence / "result.json"
    if (evidence / "result.state").exists():
        return _unmet(REASON_RESULT_TOO_LARGE, f"the result of run {earlier} was too large to keep")
    if not result_path.exists():
        return _unmet(REASON_RESULT_ABSENT, f"run {earlier} left no result")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = None
    if not (evidence / "result.index").exists() or not isinstance(result, dict):
        return _unmet(REASON_RESULT_INVALID, f"the result of run {earlier} is not a JSON object")
    for name, wanted in match.items():
        if name not in result or not _same(result[name], wanted):
            return _unmet(REASON_MATCH_MISMATCH, f"result field {name!r} of run {earlier}")
    return RELEASE


def _same(found: Any, wanted: Any) -> bool:
    """Equality of one field, JSON's: true is not 1 (Python's `==` says it is)."""
    return found == wanted and isinstance(found, bool) == isinstance(wanted, bool)
