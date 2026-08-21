"""Streaming result.json serialization with bounded index (ported from spikes/m06_projection.py)."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO

from trestle.child.index import MAX_INDEX_BYTES, Index
from trestle.common.canonical import NonCanonical


class ResultTooLarge(Exception):
    pass


def write_result(path: Path, value: Any, *, max_bytes: int | None = None) -> Index:
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("wb") as fh:
        idx = _write_value(fh, value, max_bytes=max_bytes)
    if max_bytes is not None and idx.byte_length > max_bytes:
        tmp.unlink(missing_ok=True)
        raise ResultTooLarge(idx.byte_length)
    os.replace(tmp, path)
    return idx


def _dump_scalar(value: Any) -> bytes:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise NonCanonical("non_canonical_value")
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    if value is None or isinstance(value, (bool, int, str)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    raise TypeError(type(value))


def _write_value(fh: BinaryIO, value: Any, *, max_bytes: int | None = None) -> Index:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise NonCanonical("non_canonical_value")
    if value is None or isinstance(value, (bool, int, float, str)):
        blob = _dump_scalar(value)
        fh.write(blob)
        return Index(
            root_type=type(value).__name__ if value is not None else "null",
            byte_length=len(blob),
        )
    if isinstance(value, dict):
        return _write_object(fh, value, max_bytes=max_bytes)
    if isinstance(value, (list, tuple)) or (
        isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray))
    ):
        return _write_array(fh, value, max_bytes=max_bytes)
    raise TypeError(type(value))


def _canonical_object_items(obj: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(obj.items(), key=lambda kv: kv[0])


def _write_object(fh: BinaryIO, obj: dict[str, Any], *, max_bytes: int | None = None) -> Index:
    start = fh.tell()
    fh.write(b"{")
    fields: list[dict[str, Any]] = []
    first = True
    truncated = False
    items = _canonical_object_items(obj)
    for key, val in items:
        if not first:
            fh.write(b",")
        first = False
        key_b = json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        fh.write(key_b)
        fh.write(b":")
        val_start = fh.tell()
        nested = _write_value(fh, val, max_bytes=max_bytes)
        val_end = fh.tell()
        rec = {
            "name": key,
            "start": val_start,
            "end": val_end,
            "type": nested.root_type,
            "bytes": val_end - val_start,
        }
        probe = Index(
            root_type="object",
            byte_length=0,
            fields=fields + [rec],
            field_count=len(items),
        )
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


def _write_array(fh: BinaryIO, arr: Iterable[Any], *, max_bytes: int | None = None) -> Index:
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
        nested = _write_value(fh, item, max_bytes=max_bytes)
        if max_bytes is not None and fh.tell() > max_bytes:
            raise ResultTooLarge(fh.tell())
        _ = nested
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
                ranges = ranges[:8]
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
