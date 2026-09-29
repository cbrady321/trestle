"""Run ledger — ndjson authority."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trestle.common.fsutil import append_ndjson, read_ndjson
from trestle.common.types import Handle


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
        return record

    def last_kind(self, kind: str) -> dict[str, Any] | None:
        for record in reversed(self.records):
            if record.get("kind") == kind:
                return record
        return None

    def has_kind(self, kind: str) -> bool:
        return any(record.get("kind") == kind for record in self.records)

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
        if self.has_kind("admitted"):
            return "queued"
        return "queued"


TERMINAL_KINDS = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "worker_exit", "crashed", "interrupted"}
)
_TERMINAL_KINDS = TERMINAL_KINDS


def run_dir_for(home: Path, run_id: Handle, *, month: str | None = None) -> Path:
    if month is None:
        month = time.strftime("%Y-%m")
    return home / "runs" / month / run_id


def evidence_dir(run_dir: Path) -> Path:
    return run_dir / "evidence"


def work_dir(run_dir: Path) -> Path:
    return run_dir / "work"


def ledger_path(run_dir: Path) -> Path:
    return evidence_dir(run_dir) / "ledger.ndjson"


def count_events(evidence: Path) -> int:
    """Non-blank lines of `events.ndjson`, streamed. Called once, when a run is finalized: the
    count is then recorded on the `evidence_finalized` row (MC-12) and read from there."""
    events_path = evidence / "events.ndjson"
    if not events_path.exists():
        return 0
    with events_path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
