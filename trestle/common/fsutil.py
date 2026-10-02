"""Filesystem durability helpers."""

from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any, BinaryIO


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, *, stat_from: Path | None = None) -> None:
    """tmp → fsync file → rename → fsync dir (R-STORE-8).

    The temporary file is this call's own (a fresh name beside `path`, created exclusively), so
    writers racing on one path never share it: each rename publishes one whole write, and the last
    rename wins. A failed write removes its temporary. The name keeps the `.tmp` suffix that
    recovery sweeps (`sweep_tmp_partial`), and the file is created with the mode an `open(..., "w")`
    gives (0o666 less the umask). `stat_from` copies that file's mode and times onto the
    temporary before the rename, as `shutil.copy2` would."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if stat_from is not None:
            shutil.copystat(stat_from, tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
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


# The tail check reads backwards in windows of this size, so an append's read cost is one window
# whatever the file's length (WR-EVID-4). Only a final line longer than a window costs more.
_TAIL_WINDOW = 4096


def _final_line(fh: BinaryIO, end: int) -> tuple[int, bytes, bool]:
    """The line that ends at byte offset `end` (> 0): its start offset, its bytes without the
    newline, and whether a newline ended it. Reads backwards, one window at a time."""
    chunks: list[bytes] = []
    terminated = False
    pos = end
    while pos > 0:
        low = max(0, pos - _TAIL_WINDOW)
        fh.seek(low)
        buf = fh.read(pos - low)
        if pos == end and buf.endswith(b"\n"):
            terminated = True
            buf = buf[:-1]
        cut = buf.rfind(b"\n")
        if cut >= 0:
            chunks.append(buf[cut + 1 :])
            return low + cut + 1, b"".join(reversed(chunks)), terminated
        chunks.append(buf)
        pos = low
    return 0, b"".join(reversed(chunks)), terminated


def _repair_ndjson_tail(path: Path) -> bytes:
    """Bring `path`'s tail to a line boundary and return the bytes the next write must lead with.

    Only the final line is inspected (DM-55), through one bounded window from the end of the
    file. A torn final line, newline-terminated or not, is cut with `os.truncate` at the
    previous newline (R-STORE-10). A final line that is a valid object without its newline is
    kept and terminated: the caller writes `b"\\n"` before its record, so a valid row is never
    dropped and never merged onto (WR-EVID-11).
    """
    if not path.exists():
        return b""
    with path.open("rb") as fh:
        size = end = fh.seek(0, os.SEEK_END)
        terminated = True
        while end:
            start, body, terminated = _final_line(fh, end)
            if _is_ndjson_row(body):
                break
            end = start
    if end < size:
        os.truncate(path, end)
    return b"" if terminated or not end else b"\n"


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
