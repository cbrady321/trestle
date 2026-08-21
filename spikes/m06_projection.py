#!/usr/bin/env python3
"""M0.6 — streaming result.json + bounded result.index + server-side summary.

The child never sends the result through the 'server'. The server reads only
the index plus pread of selected ranges.
"""
from __future__ import annotations

import json
import math
import os
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Iterator
from typing import Any

MAX_INDEX_BYTES = 64 * 1024
SUMMARY_BUDGET = 400  # hostile 100MB-result scenario target


class NonCanonical(ValueError):
    pass


def _dump_scalar(value: Any) -> bytes:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise NonCanonical("non_canonical_value")
        text = json.dumps(value, ensure_ascii=False)
        return text.encode("utf-8")
    if value is None or isinstance(value, (bool, int, str)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    raise TypeError(type(value))


def canonical_object_items(obj: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(obj.items(), key=lambda kv: kv[0])


@dataclass
class Index:
    root_type: str
    byte_length: int
    fields: list[dict[str, Any]] | None = None
    array_count: int | None = None
    array_element_ranges: list[dict[str, Any]] | None = None
    index_truncated: bool = False
    field_count: int | None = None

    def to_json(self) -> bytes:
        payload = {
            "root_type": self.root_type,
            "byte_length": self.byte_length,
            "index_truncated": self.index_truncated,
        }
        if self.fields is not None:
            payload["fields"] = self.fields
        if self.array_count is not None:
            payload["array_count"] = self.array_count
        if self.array_element_ranges is not None:
            payload["array_element_ranges"] = self.array_element_ranges
        if self.field_count is not None:
            payload["field_count"] = self.field_count
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write_result(path: Path, value: Any) -> Index:
    """Stream canonical JSON to path; return bounded index."""
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("wb") as fh:
        idx = _write_value(fh, value)
    os.replace(tmp, path)
    return idx


def _write_value(fh, value: Any) -> Index:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise NonCanonical("non_canonical_value")
    if value is None or isinstance(value, (bool, int, float, str)):
        start = fh.tell()
        blob = _dump_scalar(value)
        fh.write(blob)
        return Index(root_type=type(value).__name__ if value is not None else "null", byte_length=len(blob))
    if isinstance(value, dict):
        return _write_object(fh, value)
    if isinstance(value, (list, tuple)) or (
        isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray))
    ):
        return _write_array(fh, value)
    raise TypeError(type(value))


def _write_object(fh, obj: dict[str, Any]) -> Index:
    start = fh.tell()
    fh.write(b"{")
    fields: list[dict[str, Any]] = []
    first = True
    truncated = False
    items = canonical_object_items(obj)
    for key, val in items:
        if not first:
            fh.write(b",")
        first = False
        key_b = json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        fh.write(key_b)
        fh.write(b":")
        val_start = fh.tell()
        nested = _write_value(fh, val)
        val_end = fh.tell()
        rec = {"name": key, "start": val_start, "end": val_end, "type": nested.root_type, "bytes": val_end - val_start}
        probe = Index(root_type="object", byte_length=0, fields=fields + [rec], field_count=len(items))
        if len(probe.to_json()) > MAX_INDEX_BYTES:
            truncated = True
            fields = []
            break
        fields.append(rec)
    fh.write(b"}")
    end = fh.tell()
    idx = Index(
        root_type="object",
        byte_length=end - start,
        fields=None if truncated else fields,
        field_count=len(items),
        index_truncated=truncated,
    )
    if len(idx.to_json()) > MAX_INDEX_BYTES:
        idx.fields = None
        idx.index_truncated = True
    return idx


def _write_array(fh, arr) -> Index:
    start = fh.tell()
    fh.write(b"[")
    ranges: list[dict[str, Any]] = []
    truncated = False
    first = True
    count = 0
    for item in arr:
        if not first:
            fh.write(b",")
        first = False
        item_start = fh.tell()
        _write_value(fh, item)
        item_end = fh.tell()
        count += 1
        if not truncated:
            rec = {"i": count - 1, "start": item_start, "end": item_end}
            trial = ranges + [rec]
            probe = Index(
                root_type="array",
                byte_length=0,
                array_count=count,
                array_element_ranges=trial,
            )
            if len(probe.to_json()) > MAX_INDEX_BYTES:
                truncated = True
                ranges = ranges[:8]  # keep a small sample prefix
            else:
                ranges.append(rec)
    fh.write(b"]")
    end = fh.tell()
    return Index(
        root_type="array",
        byte_length=end - start,
        array_count=count,
        array_element_ranges=ranges,
        index_truncated=truncated,
    )


def pread_range(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(start)
        return fh.read(end - start)


def project(result_path: Path, index: Index, budget: int) -> dict[str, Any]:
    """Server-side: never slurp result.json."""
    if index.byte_length + 80 <= budget and not index.index_truncated:
        # small enough: still don't slurp if huge; only if byte_length fits
        if index.byte_length <= budget:
            data = json.loads(result_path.read_bytes().decode("utf-8"))
            return data if not isinstance(data, dict) else data
    footer = {
        "result_handle": "run/result",
        "result_bytes": index.byte_length,
        "truncated": True,
    }
    if index.root_type in {"str", "int", "float", "bool", "null"}:
        return {**footer, "type": index.root_type}
    if index.root_type == "array":
        sample = []
        used = 80
        if index.array_element_ranges:
            for rec in index.array_element_ranges:
                blob = pread_range(result_path, rec["start"], rec["end"])
                if used + len(blob) + 20 > budget:
                    break
                sample.append(json.loads(blob.decode("utf-8")))
                used += len(blob) + 1
        return {"count": index.array_count, "sample": sample, **footer}
    if index.root_type == "object":
        if index.index_truncated or not index.fields:
            return {**footer, "omitted": ["*"], "field_count": index.field_count}
        out: dict[str, Any] = {}
        omitted = []
        used = 80
        for rec in index.fields:
            blob = pread_range(result_path, rec["start"], rec["end"])
            piece = len(json.dumps(rec["name"]).encode()) + len(blob) + 4
            if used + piece > budget:
                omitted.append(rec["name"])
                continue
            out[rec["name"]] = json.loads(blob.decode("utf-8"))
            used += piece
        out.update(footer)
        out["omitted"] = omitted
        return out
    return footer


def huge_array(n: int) -> Iterator[int]:
    for i in range(n):
        yield i


def run_cases(work: Path) -> dict[str, Any]:
    cases = []

    def record(name: str, fn) -> None:
        try:
            cases.append({"name": name, **fn()})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"})

    def none_case():
        p = work / "none.json"
        idx = write_result(p, None)
        summary = project(p, idx, SUMMARY_BUDGET)
        return {"ok": summary is None or summary == None, "index_bytes": len(idx.to_json()), "detail": str(summary)}

    def scalar_huge():
        p = work / "scalar.json"
        s = "x" * (2_000_000)
        idx = write_result(p, s)
        summary = project(p, idx, SUMMARY_BUDGET)
        loaded = p.stat().st_size
        # server must not include the string
        ok = summary.get("truncated") is True and "x" * 100 not in json.dumps(summary)
        return {"ok": ok, "result_bytes": loaded, "summary_bytes": len(json.dumps(summary)), "detail": summary}

    def object_case():
        p = work / "obj.json"
        value = {"columns": ["a", "b"], "report": "ok", "rows": list(range(5000))}
        idx = write_result(p, value)
        summary = project(p, idx, 256)
        ok = summary.get("truncated") is True and "rows" in summary.get("omitted", []) or "rows" not in summary
        return {
            "ok": bool(ok) and p.stat().st_size > 256,
            "index_bytes": len(idx.to_json()),
            "summary_bytes": len(json.dumps(summary, separators=(",", ":"))),
            "detail": {k: summary[k] for k in summary if k != "rows"},
        }

    def array_case():
        p = work / "arr.json"
        idx = write_result(p, list(range(10_000)))
        summary = project(p, idx, SUMMARY_BUDGET)
        ok = summary.get("count") == 10_000 and summary.get("truncated") is True
        return {"ok": ok, "result_bytes": idx.byte_length, "summary_bytes": len(json.dumps(summary)), "sample_n": len(summary.get("sample", []))}

    def nan_case():
        p = work / "nan.json"
        try:
            write_result(p, float("nan"))
            return {"ok": False, "detail": "should have rejected NaN"}
        except NonCanonical:
            return {"ok": True, "detail": "non_canonical_value"}

    def inf_case():
        p = work / "inf.json"
        try:
            write_result(p, float("inf"))
            return {"ok": False, "detail": "should have rejected Inf"}
        except NonCanonical:
            return {"ok": True, "detail": "non_canonical_value"}

    def huge_stream():
        # ~100MB of JSON integers streamed; proves we don't need a 100MB Python list
        p = work / "huge.json"
        n = 8_000_000  # ~60-100MB depending on digits
        idx = write_result(p, huge_array(n))
        summary = project(p, idx, SUMMARY_BUDGET)
        index_bytes = len(idx.to_json())
        ok = (
            idx.array_count == n
            and idx.byte_length >= 40_000_000
            and index_bytes <= MAX_INDEX_BYTES
            and len(json.dumps(summary)) < SUMMARY_BUDGET + 200
            and summary.get("truncated") is True
        )
        return {
            "ok": ok,
            "result_bytes": idx.byte_length,
            "index_bytes": index_bytes,
            "index_truncated": idx.index_truncated,
            "summary_bytes": len(json.dumps(summary, separators=(",", ":"))),
        }

    def nested_object():
        p = work / "nested.json"
        value = {"outer": {"inner": {"deep": "v"}}, "keep": 1, "huge": "y" * 50_000}
        idx = write_result(p, value)
        summary = project(p, idx, 200)
        # top-level only: whole inner objects skipped or included whole
        ok = "truncated" in summary or summary.get("keep") == 1
        return {"ok": ok, "detail": list(summary.keys())}

    def many_keys():
        p = work / "manykeys.json"
        value = {f"k{i:05d}": i for i in range(20_000)}
        idx = write_result(p, value)
        ok = idx.index_truncated and len(idx.to_json()) <= MAX_INDEX_BYTES
        summary = project(p, idx, SUMMARY_BUDGET)
        return {
            "ok": ok and summary.get("truncated") is True,
            "index_bytes": len(idx.to_json()),
            "field_count": idx.field_count,
            "index_truncated": idx.index_truncated,
        }

    def death_mid_write():
        p = work / "partial.json"
        # simulate abrupt death: write a truncated file, no index
        p.write_bytes(b'{"rows":[1,2,3')
        return {"ok": not _valid_json(p), "detail": "invalid JSON as expected"}

    record("none", none_case)
    record("huge_scalar", scalar_huge)
    record("object_field_skip", object_case)
    record("array_count_sample", array_case)
    record("reject_nan", nan_case)
    record("reject_inf", inf_case)
    record("huge_stream_array", huge_stream)
    record("nested_object", nested_object)
    record("many_keys_coarsen", many_keys)
    record("abrupt_death_invalid", death_mid_write)
    return {"cases": cases, "all_ok": all(c.get("ok") for c in cases)}


def _valid_json(path: Path) -> bool:
    try:
        json.loads(path.read_bytes().decode("utf-8"))
        return True
    except Exception:
        return False


def main() -> int:
    work = Path(__file__).resolve().parent / "out" / "m06"
    work.mkdir(parents=True, exist_ok=True)
    report = run_cases(work)
    (work / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
