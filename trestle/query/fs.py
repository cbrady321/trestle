"""Filesystem query backend — scans runs/ as derived cache."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trestle.common import codes
from trestle.common.limits import CaptureLimits, capture_limits
from trestle.common.types import Handle, RequestOutcome
from trestle.query import views as view_defs
from trestle.query.conformance import validate_envelope
from trestle.server.ledger import TERMINAL_KINDS, RunLedger, evidence_dir, ledger_path
from trestle.server.pins import PinStore

_FAILURE_STATES = TERMINAL_KINDS - frozenset({"succeeded"})


@dataclass(frozen=True)
class RunRecord:
    run_id: Handle
    run_dir: Path
    plugin: str
    version: str
    state: str
    started_at: str
    ended_at: str | None
    failed_at: str | None
    finalized: bool
    snapshot_id: str
    spec_hash: str
    args_hash: str
    source_sha256: str
    resolved_artifacts: dict[str, str]


class FilesystemQueryBackend:
    backend = view_defs.BACKEND_NAME

    def __init__(self, home: Path, *, limits: CaptureLimits | None = None) -> None:
        self.home = home
        self.limits = limits or capture_limits()
        self._cache_token: str | None = None
        self._cache_runs: list[RunRecord] = []

    def query(
        self,
        view: str,
        params: dict[str, object],
        cursor: Handle | None = None,
    ) -> dict[str, Any] | RequestOutcome:
        if view not in view_defs.VIEW_NAMES:
            return RequestOutcome(
                code=codes.INVALID_VIEW,
                message=f"unknown view: {view}",
                retryable=False,
                origin="projection",
            )

        runs, scan_truncated = self._load_runs()
        as_of = _now_iso()
        offset = 0
        token = self._cache_token or ""

        if cursor is not None:
            decoded = view_defs.decode_cursor(cursor)
            if decoded is None:
                return RequestOutcome(
                    code=codes.CURSOR_EXPIRED,
                    message="invalid or stale cursor",
                    retryable=True,
                    origin="projection",
                )
            cursor_view, offset, token = decoded
            if cursor_view != view or token != self._cache_token:
                return RequestOutcome(
                    code=codes.CURSOR_EXPIRED,
                    message="invalid or stale cursor",
                    retryable=True,
                    origin="projection",
                )

        run_id = params.get("run_id")
        if view in view_defs.RUN_SCOPED_VIEWS:
            if not isinstance(run_id, str) or not run_id:
                return RequestOutcome(
                    code=codes.PROJECTION_INVALID_ARGS,
                    message=f"run_id required for view {view}",
                    retryable=False,
                    origin="projection",
                )
            record = self._find_run(runs, run_id)
            if record is None:
                return RequestOutcome(
                    code=codes.INVALID_HANDLE,
                    message=f"unknown run: {run_id}",
                    retryable=False,
                    origin="projection",
                )
            if view in view_defs.FINALIZED_VIEWS and not record.finalized:
                return RequestOutcome(
                    code=codes.NOT_FINALIZED,
                    message=f"evidence not finalized for run: {run_id}",
                    retryable=True,
                    origin="projection",
                )
            rows = self._rows_for_run_view(view, record)
        elif view == "recent_runs":
            rows = [_recent_run_row(record) for record in runs]
        elif view == "recent_failures":
            rows = [
                _recent_failure_row(record)
                for record in runs
                if record.finalized and record.state in _FAILURE_STATES
            ]
        elif view == "artifact_refs":
            artifact_id = params.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id:
                return RequestOutcome(
                    code=codes.PROJECTION_INVALID_ARGS,
                    message="artifact_id required for view artifact_refs",
                    retryable=False,
                    origin="projection",
                )
            rows = self._artifact_ref_rows(runs, artifact_id)
        else:
            rows = []

        items, next_cursor, page_truncated = view_defs.paginate_rows(
            rows,
            view=view,
            offset=offset,
            token=token,
        )
        envelope = view_defs.bounded_envelope(
            view=view,
            items=items,
            next_cursor=next_cursor,
            truncated=page_truncated or scan_truncated,
            as_of=as_of,
        )
        errors = validate_envelope(view, envelope)
        if errors:
            raise RuntimeError(f"query conformance failed for {view}: {errors[0]}")
        return envelope

    def _load_runs(self) -> tuple[list[RunRecord], bool]:
        runs_root = self.home / "runs"
        if not runs_root.exists():
            self._cache_token = "empty"
            self._cache_runs = []
            return [], False

        candidates: list[tuple[str, Path]] = []
        scan_bytes = 0
        scan_truncated = False
        deadline = time.monotonic() + (self.limits.max_scan_time_ms / 1000.0)

        for month_dir in sorted(runs_root.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue
            for run_dir in sorted(month_dir.iterdir(), reverse=True):
                if time.monotonic() > deadline:
                    scan_truncated = True
                    break
                if not run_dir.is_dir():
                    continue
                path = ledger_path(run_dir)
                if not path.exists():
                    continue
                size = path.stat().st_size
                scan_bytes += size
                if scan_bytes > self.limits.max_scan_bytes:
                    scan_truncated = True
                    break
                candidates.append((run_dir.name, run_dir))
                if len(candidates) >= view_defs.RECENCY_CACHE_SIZE:
                    break
            if scan_truncated or len(candidates) >= view_defs.RECENCY_CACHE_SIZE:
                break

        candidates.sort(key=lambda item: item[0], reverse=True)
        token = hashlib.sha256(
            ",".join(run_id for run_id, _ in candidates).encode("utf-8")
        ).hexdigest()[:16]

        if token == self._cache_token and self._cache_runs:
            return self._cache_runs, scan_truncated

        records: list[RunRecord] = []
        for run_id, run_dir in candidates:
            record = _load_run_record(run_id, run_dir)
            if record is not None:
                records.append(record)

        self._cache_token = token
        self._cache_runs = records
        return records, scan_truncated

    @staticmethod
    def _find_run(runs: list[RunRecord], run_id: str) -> RunRecord | None:
        for record in runs:
            if record.run_id == run_id:
                return record
        return None

    def _rows_for_run_view(self, view: str, record: RunRecord) -> list[dict[str, object]]:
        if view == "run":
            return [_run_row(record)]
        if view == "last_error":
            return _last_error_rows(record)
        if view == "run_tail":
            return _run_tail_rows(record)
        if view == "run_events":
            return _run_events_rows(record)
        if view == "run_provenance":
            return [_run_provenance_row(record)]
        if view == "run_artifacts":
            return _run_artifact_rows(record, self.home)
        return []

    def _artifact_ref_rows(
        self,
        runs: list[RunRecord],
        artifact_id: str,
    ) -> list[dict[str, object]]:
        producer: str | None = None
        for record in runs:
            ledger = RunLedger.open(ledger_path(record.run_dir))
            for item in ledger.records:
                if item.get("kind") != "artifact_available":
                    continue
                if str(item.get("artifact_id", "")) == artifact_id:
                    producer = record.run_id
                    break
            if producer is not None:
                break

        referrers: list[str] = []
        for record in runs:
            if artifact_id in record.resolved_artifacts.values():
                referrers.append(record.run_id)
        referrers.sort()

        rows: list[dict[str, object]] = []
        if producer is not None:
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "producer_run_id": producer,
                    "referrer_run_id": producer,
                }
            )
        for referrer in referrers:
            if referrer == producer:
                continue
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "producer_run_id": producer or referrer,
                    "referrer_run_id": referrer,
                }
            )
        return rows


def _load_run_record(run_id: str, run_dir: Path) -> RunRecord | None:
    path = ledger_path(run_dir)
    if not path.exists():
        return None
    ledger = RunLedger.open(path)
    created = ledger.last_kind("created")
    if created is None:
        return None

    spec_path = evidence_dir(run_dir) / "spec.json"
    spec: dict[str, object] = {}
    if spec_path.exists():
        loaded = json.loads(spec_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            spec = loaded

    started_at = str(created.get("at", ""))
    ended = ledger.last_kind("execution_ended")
    ended_at = str(ended.get("at")) if ended is not None else None

    failed_at: str | None = None
    state = ledger.projected_state()
    if state in _FAILURE_STATES:
        terminal = ledger.last_kind(state)
        if terminal is not None:
            failed_at = str(terminal.get("at", ""))

    resolved_raw = spec.get("resolved_artifacts", {})
    resolved: dict[str, str] = {}
    if isinstance(resolved_raw, dict):
        resolved = {str(k): str(v) for k, v in resolved_raw.items()}

    return RunRecord(
        run_id=run_id,
        run_dir=run_dir,
        plugin=str(created.get("plugin", spec.get("plugin", ""))),
        version=str(created.get("version", spec.get("version", ""))),
        state=state,
        started_at=started_at,
        ended_at=ended_at,
        failed_at=failed_at,
        finalized=ledger.has_kind("evidence_finalized"),
        snapshot_id=str(spec.get("snapshot_id", "")),
        spec_hash=str(created.get("spec_hash", "")),
        args_hash=str(spec.get("args_hash", "")),
        source_sha256=str(spec.get("source_sha256", "")),
        resolved_artifacts=resolved,
    )


def _run_row(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "plugin": record.plugin,
        "version": record.version,
        "state": record.state,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
    }


def _recent_run_row(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "plugin": record.plugin,
        "state": record.state,
        "started_at": record.started_at,
    }


def _recent_failure_row(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "plugin": record.plugin,
        "state": record.state,
        "failed_at": record.failed_at or record.ended_at or record.started_at,
    }


def _run_provenance_row(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "snapshot_id": record.snapshot_id,
        "spec_hash": record.spec_hash,
        "args_hash": record.args_hash,
        "source_sha256": record.source_sha256,
    }


def _last_error_rows(record: RunRecord) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    events_path = evidence_dir(record.run_dir) / "events.ndjson"
    if events_path.exists():
        for seq, line in enumerate(
            events_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind", ""))
            if kind != "error":
                continue
            payload = event.get("payload", {})
            message = ""
            if isinstance(payload, dict):
                message = str(payload.get("message", payload))
            rows.append(
                {
                    "run_id": record.run_id,
                    "event_seq": seq,
                    "kind": kind,
                    "message": message,
                    "at": str(event.get("at", "")),
                }
            )
    if not rows and record.state in _FAILURE_STATES:
        ledger = RunLedger.open(ledger_path(record.run_dir))
        terminal = ledger.last_kind(record.state)
        if terminal is not None:
            rows.append(
                {
                    "run_id": record.run_id,
                    "event_seq": int(terminal.get("seq", 0)),
                    "kind": record.state,
                    "message": str(terminal.get("classification", record.state)),
                    "at": str(terminal.get("at", "")),
                }
            )
    return rows


def _run_events_rows(record: RunRecord) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    events_path = evidence_dir(record.run_dir) / "events.ndjson"
    if not events_path.exists():
        return rows
    for seq, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            payload_obj: object = payload
        else:
            payload_obj = dict(payload)
        rows.append(
            {
                "run_id": record.run_id,
                "event_seq": seq,
                "kind": str(event.get("kind", "")),
                "payload": payload_obj,
            }
        )
    return rows


def _run_tail_rows(record: RunRecord) -> list[dict[str, object]]:
    console_dir = evidence_dir(record.run_dir) / "console"
    lines: list[str] = []
    for name in ("stdout.log", "stderr.log"):
        path = console_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text:
            continue
        encoded = text.encode("utf-8")
        if len(encoded) > view_defs.CONSOLE_TAIL_BYTES:
            tail = encoded[-view_defs.CONSOLE_TAIL_BYTES :].decode("utf-8", errors="replace")
            lines.extend(tail.splitlines())
        else:
            lines.extend(text.splitlines())

    rows: list[dict[str, object]] = []
    for line_no, text in enumerate(lines, start=1):
        rows.append({"run_id": record.run_id, "line_no": line_no, "text": text})
    return rows


def _run_artifact_rows(record: RunRecord, home: Path) -> list[dict[str, object]]:
    pins = PinStore.open(home)
    ledger = RunLedger.open(ledger_path(record.run_dir))
    rows: list[dict[str, object]] = []
    for item in ledger.records:
        if item.get("kind") != "artifact_available":
            continue
        artifact_id = str(item.get("artifact_id", ""))
        name = str(item.get("name", artifact_id))
        path = evidence_dir(record.run_dir) / "artifacts" / artifact_id
        state = "available" if path.exists() else "missing"
        retention_class = "pinned" if pins.is_pinned(artifact_id) else "default"
        rows.append(
            {
                "run_id": record.run_id,
                "artifact_id": artifact_id,
                "name": name,
                "retention_class": retention_class,
                "state": state,
            }
        )
    return rows


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
