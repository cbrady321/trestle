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
    """Append one JSON line and fsync (R-STORE-9).

    Self-verifying framing (K-18): the tail is repaired before the record is written, so the
    record never merges onto what precedes it. A final line that parses as one JSON object but
    lacks its newline is terminated in the same write as the record; no committed byte is
    rewritten (hld-wr-run-record Constraint (d)).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = _repair_ndjson_tail(path)
    line = prefix + json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    with path.open("ab") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    fsync_dir(path.parent)


def _is_ndjson_row(line: bytes) -> bool:
    """One JSON object: what a complete record is."""
    try:
        return isinstance(json.loads(line.decode("utf-8")), dict)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError
        return False


def _repair_ndjson_tail(path: Path) -> bytes:
    """Bring `path`'s tail to a line boundary and return the bytes the next write must lead with.

    Only the final line is inspected (DM-55). A torn final line, newline-terminated or not, is cut
    with `os.truncate` at the previous newline (R-STORE-10). A final line that is a valid object
    without its newline is kept and terminated: the caller writes `b"\\n"` before its record, so
    a valid row is never dropped and never merged onto (WR-EVID-11).
    """
    if not path.exists():
        return b""
    data = path.read_bytes()
    size = end = len(data)
    while end:
        body_end = end - 1 if data[end - 1 : end] == b"\n" else end
        start = data.rfind(b"\n", 0, body_end) + 1
        if _is_ndjson_row(data[start:body_end]):
            break
        end = start
    if end < size:
        os.truncate(path, end)
    return b"\n" if end and data[end - 1 : end] != b"\n" else b""


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    """The rows of `path`, in order. A valid final line without its newline is a row; an
    unparseable line ends the read (a torn tail, R-STORE-10), so nothing after it is merged in."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_bytes().split(b"\n"):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line.decode("utf-8")))
        except ValueError:
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
