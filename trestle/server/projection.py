"""Server-side summary projection from result.index + pread (R-INV-1)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trestle.child.index import Index
from trestle.common import codes
from trestle.common.fsutil import atomic_write_json
from trestle.common.limits import CaptureLimits, capture_limits
from trestle.common.types import RequestOutcome


def _window_int(window: dict[str, object], key: str, default: int) -> int:
    return _as_int(window.get(key, default), default)


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    return default


def result_handle(run_id: str) -> str:
    return f"{run_id}/result"


def pread_range(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(start)
        return fh.read(end - start)


@dataclass
class SummaryProjection:
    summary: Any
    truncated: bool
    omitted: list[str] | None
    next_handle: str | None
    result_bytes: int | None


def load_index(evidence: Path) -> Index | None:
    index_path = evidence / "result.index"
    if not index_path.exists():
        return None
    return Index.from_json(index_path.read_bytes())


def build_summary(
    *,
    run_id: str,
    result_path: Path,
    index: Index,
    budget: int,
) -> SummaryProjection:
    if index.byte_length <= budget and not index.index_truncated:
        blob = pread_range(result_path, 0, index.byte_length)
        value = json.loads(blob.decode("utf-8"))
        return SummaryProjection(
            summary=value,
            truncated=False,
            omitted=None,
            next_handle=None,
            result_bytes=index.byte_length,
        )

    handle = result_handle(run_id)
    if index.root_type in {"str", "int", "float", "bool", "null"}:
        return SummaryProjection(
            summary=handle,
            truncated=True,
            omitted=None,
            next_handle=None,
            result_bytes=index.byte_length,
        )

    if index.root_type == "array":
        sample: list[Any] = []
        used = 80
        if index.array_element_ranges:
            for rec in index.array_element_ranges:
                blob = pread_range(result_path, rec["start"], rec["end"])
                if used + len(blob) + 20 > budget:
                    break
                sample.append(json.loads(blob.decode("utf-8")))
                used += len(blob) + 1
        return SummaryProjection(
            summary={"count": index.array_count, "sample": sample, "handle": handle},
            truncated=True,
            omitted=None,
            next_handle=None,
            result_bytes=index.byte_length,
        )

    if index.root_type == "object":
        if index.index_truncated or not index.fields:
            return SummaryProjection(
                summary={"field_count": index.field_count},
                truncated=True,
                omitted=["*"],
                next_handle=handle,
                result_bytes=index.byte_length,
            )
        out: dict[str, Any] = {}
        omitted: list[str] = []
        used = 80
        for rec in index.fields:
            blob = pread_range(result_path, rec["start"], rec["end"])
            piece = len(json.dumps(rec["name"]).encode()) + len(blob) + 4
            if used + piece > budget:
                omitted.append(rec["name"])
                continue
            out[rec["name"]] = json.loads(blob.decode("utf-8"))
            used += piece
        return SummaryProjection(
            summary=out,
            truncated=True,
            omitted=omitted,
            next_handle=handle,
            result_bytes=index.byte_length,
        )

    return SummaryProjection(
        summary=None,
        truncated=True,
        omitted=None,
        next_handle=handle,
        result_bytes=index.byte_length,
    )


def write_summary_json(evidence: Path, projection: SummaryProjection, budget: int) -> None:
    payload = {
        "summary": projection.summary,
        "truncated": projection.truncated,
        "omitted": projection.omitted,
        "next": projection.next_handle,
        "result_bytes": projection.result_bytes,
        "summary_budget": budget,
        "algorithm": "index+pread-v1",
    }
    atomic_write_json(evidence / "summary.json", payload)


def _invalid_handle(message: str) -> RequestOutcome:
    return RequestOutcome(
        code=codes.INVALID_HANDLE,
        message=message,
        retryable=False,
        origin="projection",
    )


def _invalid_args(message: str) -> RequestOutcome:
    return RequestOutcome(
        code=codes.PROJECTION_INVALID_ARGS,
        message=message,
        retryable=False,
        origin="projection",
    )


def _missing(message: str) -> RequestOutcome:
    return RequestOutcome(
        code=codes.MISSING,
        message=message,
        retryable=False,
        origin="projection",
    )


def parse_result_target(target: str) -> str | None:
    if target.endswith("/result"):
        return target[: -len("/result")]
    return None


def fetch_bytes(
    *,
    home: Path,
    target: str,
    window: dict[str, object],
) -> dict[str, object] | RequestOutcome:
    if target.startswith("/") or ".." in target or "\\" in target:
        return _invalid_handle("path-shaped targets are forbidden")

    kind = str(window.get("kind", "tail"))
    limits = capture_limits()

    if target.startswith("art_"):
        return _fetch_artifact(home, target, kind, window, limits)

    run_id = parse_result_target(target)
    if run_id is not None:
        return _fetch_result(home, run_id, target, kind, window, limits)

    return _invalid_handle(f"unknown handle: {target}")


def _find_run_dir(home: Path, run_id: str) -> Path | None:
    runs_root = home / "runs"
    if not runs_root.exists():
        return None
    for month_dir in runs_root.iterdir():
        candidate = month_dir / run_id
        if candidate.is_dir():
            return candidate
    return None


def _fetch_artifact(
    home: Path,
    artifact_id: str,
    kind: str,
    window: dict[str, object],
    limits: Any,
) -> dict[str, object] | RequestOutcome:
    if kind not in {"range", "head", "tail", "grep"}:
        return _invalid_args(f"window kind {kind!r} not permitted for artifacts")
    path = _locate_artifact(home, artifact_id)
    if path is None:
        return _missing(f"artifact not found: {artifact_id}")
    data = path.read_bytes()
    if _looks_binary(data):
        return {
            "tag": "binary_meta",
            "source": artifact_id,
            "size": len(data),
            "mime": None,
            "truncated": False,
        }
    return _fetch_text(data, artifact_id, kind, window, limits)


def _locate_artifact(home: Path, artifact_id: str) -> Path | None:
    runs_root = home / "runs"
    if not runs_root.exists():
        return None
    for month_dir in runs_root.iterdir():
        for run_dir in month_dir.iterdir():
            candidate = run_dir / "evidence" / "artifacts" / artifact_id
            if candidate.exists():
                return candidate
    return None


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return True
    text = data[:8192]
    try:
        text.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _decode_lines(data: bytes) -> tuple[list[str], bool]:
    text = data.decode("utf-8", errors="replace")
    invalid = "\ufffd" in text and any(b >= 0x80 for b in data)
    lines = text.splitlines()
    return lines, invalid


def _fetch_text(
    data: bytes,
    source: str,
    kind: str,
    window: dict[str, object],
    limits: CaptureLimits,
) -> dict[str, object]:
    scan_bytes = min(len(data), limits.max_scan_bytes)
    lines, invalid_utf8 = _decode_lines(data[:scan_bytes])
    total = len(lines)
    truncated = scan_bytes < len(data)

    if kind == "grep":
        pattern = str(window.get("pattern", ""))
        matches = []
        for idx, line in enumerate(lines, start=1):
            if pattern in line:
                matches.append({"line_no": idx, "text": line[:512]})
            if len(matches) >= 50:
                truncated = True
                break
        out: dict[str, object] = {
            "tag": "grep_matches",
            "source": source,
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
            "scan_bytes": scan_bytes,
        }
        if invalid_utf8:
            out["invalid_utf8"] = True
        return out

    if kind == "head":
        count = _window_int(window, "count", 50)
        selected = lines[:count]
        start_line = 1 if selected else 0
        end_line = len(selected)
    elif kind == "range":
        start_line = max(1, _window_int(window, "start_line", 1))
        end_line = max(start_line, _window_int(window, "end_line", start_line))
        selected = lines[start_line - 1 : end_line]
    else:  # tail
        count = _window_int(window, "count", 50)
        selected = lines[-count:] if count else []
        if selected:
            start_line = total - len(selected) + 1
            end_line = total
        else:
            start_line = 0
            end_line = 0

    if len(selected) < (end_line - start_line + 1 if end_line else 0):
        truncated = True

    out = {
        "tag": "text",
        "source": source,
        "lines": selected,
        "fraction": {"start_line": start_line, "end_line": end_line, "total_lines": total},
        "truncated": truncated,
        "scan_bytes": scan_bytes,
    }
    if invalid_utf8:
        out["invalid_utf8"] = True
    return out


def _fetch_result(
    home: Path,
    run_id: str,
    target: str,
    kind: str,
    window: dict[str, object],
    limits: CaptureLimits,
) -> dict[str, object] | RequestOutcome:
    if kind not in {"jsonpath", "range", "head", "tail"}:
        return _invalid_args(f"window kind {kind!r} not permitted for result handles")
    run_dir = _find_run_dir(home, run_id)
    if run_dir is None:
        return _invalid_handle(f"unknown run: {run_id}")
    evidence = run_dir / "evidence"
    result_path = evidence / "result.json"
    index = load_index(evidence)
    if index is None or not result_path.exists():
        return _missing(f"result not available for {run_id}")

    if kind == "jsonpath":
        expr = str(window.get("expr", "$"))
        return _fetch_jsonpath(result_path, index, target, expr, limits)

    data = pread_range(result_path, 0, min(index.byte_length, limits.max_scan_bytes))
    return _fetch_text(data, target, kind, window, limits)


def _fetch_jsonpath(
    result_path: Path,
    index: Index,
    source: str,
    expr: str,
    limits: CaptureLimits,
) -> dict[str, object]:
    started = time.monotonic()
    values: list[Any] = []
    scan_bytes = 0
    truncated = False

    if expr in {"$[*]", "$.*"} and index.root_type == "array" and index.array_element_ranges:
        for rec in index.array_element_ranges:
            if time.monotonic() - started > limits.max_scan_time_ms / 1000.0:
                truncated = True
                break
            blob = pread_range(result_path, rec["start"], rec["end"])
            scan_bytes += len(blob)
            if scan_bytes > limits.max_scan_bytes:
                truncated = True
                break
            values.append(json.loads(blob.decode("utf-8")))
            if len(values) >= 50:
                truncated = True
                break
        return {
            "tag": "jsonpath_matches",
            "source": source,
            "values": values,
            "truncated": truncated,
            "scan_bytes": scan_bytes,
        }

    if expr.startswith("$.") and index.root_type == "object" and index.fields:
        field = expr[2:]
        for rec in index.fields:
            if rec["name"] != field:
                continue
            blob = pread_range(result_path, rec["start"], rec["end"])
            scan_bytes = len(blob)
            values.append(json.loads(blob.decode("utf-8")))
            break
        return {
            "tag": "jsonpath_matches",
            "source": source,
            "values": values,
            "truncated": False,
            "scan_bytes": scan_bytes,
        }

    if index.byte_length <= limits.max_scan_bytes:
        blob = pread_range(result_path, 0, index.byte_length)
        scan_bytes = len(blob)
        values.append(json.loads(blob.decode("utf-8")))
    else:
        truncated = True
        scan_bytes = limits.max_scan_bytes

    return {
        "tag": "jsonpath_matches",
        "source": source,
        "values": values,
        "truncated": truncated,
        "scan_bytes": scan_bytes,
    }
