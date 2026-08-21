"""Filesystem durability helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """tmp → fsync file → rename → fsync dir (R-STORE-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def atomic_write_json(path: Path, obj: Any) -> None:
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    atomic_write(path, data)


def append_ndjson(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line and fsync (R-STORE-9)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _truncate_partial_ndjson_tail(path)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    with path.open("ab") as fh:
        fh.write(line.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    fsync_dir(path.parent)


def _truncate_partial_ndjson_tail(path: Path) -> None:
    """Drop torn trailing lines before appending (R-STORE-10)."""
    if not path.exists() or path.stat().st_size == 0:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    original_len = len(lines)
    while lines:
        try:
            json.loads(lines[-1])
            break
        except json.JSONDecodeError:
            lines.pop()
    if not lines:
        path.unlink(missing_ok=True)
        return
    if len(lines) == original_len:
        return
    payload = "\n".join(lines) + "\n"
    atomic_write(path, payload.encode("utf-8"))


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break  # trailing_partial_record tolerated (R-STORE-10)
    return records


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
