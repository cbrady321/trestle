"""Run ledger — ndjson authority."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trestle.common.fsutil import append_ndjson, atomic_write_json, read_ndjson
from trestle.common.ids import run_id_recency
from trestle.common.types import Handle

LEDGER_FILE = "ledger.ndjson"
STATE_FILE = "state.json"


@dataclass
class RunLedger:
    path: Path
    records: list[dict[str, Any]]

    @classmethod
    def open(cls, path: Path) -> RunLedger:
        return cls(path=path, records=read_ndjson(path))

    def append(self, kind: str, **fields: Any) -> dict[str, Any]:
        seq = len(self.records) + 1
        record: dict[str, Any] = {
            "seq": seq,
            "kind": kind,
            "at": _now_iso(),
            **fields,
        }
        append_ndjson(self.path, record)
        self.records.append(record)
        # v0.4 Problem C: the ledger row first (the authority), then evidence/state.json by atomic
        # rename. Only the run's lock holder appends, so only it writes state.json.
        if kind in STATE_KINDS and self.path.name == LEDGER_FILE:
            write_state(self.path.parent, self.records)
        return record

    def last_kind(self, kind: str) -> dict[str, Any] | None:
        record: dict[str, Any]
        for record in reversed(self.records):
            if record.get("kind") == kind:
                return record
        return None

    def has_kind(self, kind: str) -> bool:
        return any(record.get("kind") == kind for record in self.records)

    def released_deadline(self) -> str | None:
        """The deadline a held run's `released` row minted (ISO, release time + `deadline_s`), or
        None: a run that was never held keeps `spec.deadline` (Feature 3)."""
        released = self.last_kind("released")
        minted = released.get("deadline") if released is not None else None
        return minted if isinstance(minted, str) else None

    def terminal_state(self) -> str | None:
        for record in reversed(self.records):
            kind = record.get("kind")
            if kind in TERMINAL_KINDS:
                return str(kind)
        return None

    def projected_state(self) -> str:
        if not self.has_kind("created"):
            return "queued"
        if self.has_kind("evidence_finalized"):
            terminal = self.terminal_state()
            if terminal:
                return terminal
        if self.has_kind("execution_ended"):
            return "running"
        if self.has_kind("started"):
            return "running"
        # Feature 3: a `held` row with no `released` row after it is the held state; a run that
        # ends held (cancelled, unmet, reaped) reaches its terminal row through the branch above
        if self.has_kind("held") and not self.has_kind("released"):
            return "held"
        if self.has_kind("admitted"):
            return "queued"
        return "queued"


TERMINAL_KINDS = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "worker_exit", "crashed", "interrupted"}
)
_TERMINAL_KINDS = TERMINAL_KINDS
NON_TERMINAL_STATES = frozenset({"queued", "running", "held"})

# The rows that change what evidence/state.json says (v0.4 Problem C); state.json is rewritten
# after each of them, and after no other row.
STATE_KINDS = frozenset(
    {
        "created",
        "held",
        "released",
        "admitted",
        "started",
        "execution_ended",
        "artifact_available",
        "limit_exceeded",
        "evidence_finalized",
        *TERMINAL_KINDS,
    }
)


def state_record(evidence: Path, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What evidence/state.json holds for a ledger's rows (None before the `created` row): the
    small file status(), the waiters and the run listings read instead of the ledger. `seq` is the
    last ledger row it reflects."""
    ledger = RunLedger(path=evidence / LEDGER_FILE, records=records)
    created = ledger.last_kind("created")
    if created is None:
        return None
    state = ledger.projected_state()
    started = ledger.last_kind("started")
    ended = ledger.last_kind("execution_ended")
    terminal = ledger.last_kind(state) if state in TERMINAL_KINDS else None
    limits = ledger.last_kind("limit_exceeded")
    markers = limits.get("markers") if limits is not None else None
    duration = ended.get("duration_ms") if ended is not None else None
    deadline_s = created.get("deadline_s")
    if not isinstance(deadline_s, int | float) or isinstance(deadline_s, bool):
        deadline_s = _spec_timeout_s(evidence)
    return {
        "run_id": created.get("run_id"),
        "plugin": created.get("plugin"),
        "version": created.get("version"),
        "owner": created.get("owner"),
        "spec_hash": created.get("spec_hash"),
        "key": created.get("idempotency_key"),
        "retry_of": created.get("retry_of"),
        "after_run_id": created.get("after_run_id"),
        "deadline_s": deadline_s,
        "deadline_source": created.get("deadline_source"),
        "state": state,
        "finalized": ledger.has_kind("evidence_finalized"),
        "created_at": created.get("at"),
        "started_at": started.get("at") if started is not None else None,
        "ended_at": ended.get("at") if ended is not None else None,
        "terminal_at": terminal.get("at") if terminal is not None else None,
        "duration_ms": duration if isinstance(duration, int) else None,
        "artifact_count": sum(1 for r in records if r.get("kind") == "artifact_available"),
        "limits_exceeded": markers if isinstance(markers, list) else None,
        # where the run's summary comes from once it is terminal: the finalized answer
        "summary": "evidence/answer.json" if terminal is not None else None,
        "seq": int(records[-1].get("seq") or len(records)) if records else 0,
    }


def write_state(evidence: Path, records: list[dict[str, Any]]) -> None:
    record = state_record(evidence, records)
    if record is not None:
        atomic_write_json(evidence / STATE_FILE, record)


def _spec_timeout_s(evidence: Path) -> float | None:
    """A run whose `created` row predates `deadline_s`: its spec's `timeout_s`."""
    try:
        spec = json.loads((evidence / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    timeout = spec.get("timeout_s") if isinstance(spec, dict) else None
    return float(timeout) if isinstance(timeout, int | float) else None


def run_dir_for(home: Path, run_id: Handle, *, month: str | None = None) -> Path:
    if month is None:
        month = time.strftime("%Y-%m")
    return home / "runs" / month / run_id


def is_hidden(path: Path) -> bool:
    """A name starting with a dot under runs/ is never a run: an admission in flight builds its
    run as `.adm-<run_id>` (v0.4 rule 1), and every walker of runs/ skips such names."""
    return path.name.startswith(".")


def iter_run_dirs(home: Path, *, reverse: bool = False) -> Iterator[Path]:
    """Every run directory under `home/runs/<month>/`, in time order within a month (`reverse`:
    newest first, by the timestamp the run id carries: ids do not sort by time as text), skipping
    files and dot names at both levels."""
    runs_root = home / "runs"
    if not runs_root.is_dir():
        return
    for month_dir in sorted(runs_root.iterdir(), reverse=reverse):
        if not month_dir.is_dir() or is_hidden(month_dir):
            continue
        for run_dir in sorted(
            month_dir.iterdir(), key=lambda p: run_id_recency(p.name), reverse=reverse
        ):
            if run_dir.is_dir() and not is_hidden(run_dir):
                yield run_dir


def evidence_dir(run_dir: Path) -> Path:
    return run_dir / "evidence"


def work_dir(run_dir: Path) -> Path:
    return run_dir / "work"


def ledger_path(run_dir: Path) -> Path:
    return evidence_dir(run_dir) / LEDGER_FILE


def state_path(run_dir: Path) -> Path:
    return evidence_dir(run_dir) / STATE_FILE


def read_state(run_dir: Path) -> dict[str, Any] | None:
    """The run's evidence/state.json, or None when it is missing (a run admitted before v0.4) or
    unreadable. Lock-free: it is replaced by atomic rename."""
    try:
        loaded = json.loads(state_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) and isinstance(loaded.get("state"), str) else None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
