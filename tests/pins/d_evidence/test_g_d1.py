"""G-D1 (BFD-31, K-18): `append_ndjson` re-reads the whole file on every
append, so the bytes read per append equal the file size and grow without
bound. Operation count only; no wall clock (SA-05)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pins.d_evidence._bytes_read import count_bytes_read
from tests.proof.markers import TargetUnmet, target_check
from trestle.common.fsutil import append_ndjson
from trestle.server.ledger import ledger_path

SMALL_ROWS = 2000
LARGE_ROWS = 4000


def _prepare(tmp_path: Path, rows: int) -> Path:
    """A ledger file of `rows` valid rows, written directly (the append
    under test is the next one)."""
    path = ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"kind": "log", "seq": i, "pad": "x" * 40}) + "\n" for i in range(rows)]
    path.write_bytes("".join(lines).encode("utf-8"))
    return path


def _bytes_read_by_append(path: Path) -> int:
    with count_bytes_read(path) as counter:
        append_ndjson(path, {"kind": "log", "seq": -1})
    return counter.bytes_read


@pytest.mark.pin("G-D1")
def test_pin_bytes_read_per_append_equals_file_size(tmp_path: Path) -> None:
    path = _prepare(tmp_path, SMALL_ROWS)
    size = path.stat().st_size
    assert _bytes_read_by_append(path) == size


@pytest.mark.target("G-D1")
@pytest.mark.proves("WR-EVID-4", "A9.2", "core", "core", "must", "CI")
@pytest.mark.proves("WR-EVID-4", "WR-EVID-4:flat-append", "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, raises=TargetUnmet, reason="defect:G-D1")
def test_target_bytes_read_nongrowing_2000_vs_4000(tmp_path: Path) -> None:
    small = _bytes_read_by_append(_prepare(tmp_path / "small", SMALL_ROWS))
    large = _bytes_read_by_append(_prepare(tmp_path / "large", LARGE_ROWS))
    target_check(
        large <= small,
        "G-D1",
        f"bytes read per append grew with the file: {small} at {SMALL_ROWS} rows, "
        f"{large} at {LARGE_ROWS} rows",
    )
