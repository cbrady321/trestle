"""G-D2 (WR-EVID-11, DM-55), flipped by L.CS-1.1: a valid record written without its
trailing newline is terminated by the next append, so no row is merged and none is lost."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.proof.markers import target_check
from trestle.common.fsutil import append_ndjson, read_ndjson
from trestle.server.ledger import ledger_path

FIRST = {"kind": "first", "n": 1}
SECOND = {"kind": "second", "n": 2}
THIRD = {"kind": "third", "n": 3}


def _plant(tmp_path: Path) -> Path:
    """Ledger whose only row is valid JSON with no trailing newline, then
    two ordinary appends."""
    path = ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(FIRST, separators=(",", ":")).encode("utf-8"))
    append_ndjson(path, SECOND)
    append_ndjson(path, THIRD)
    return path


@pytest.mark.proves("WR-EVID-11", "A9.4", "core", "core", "must", "CI")
@pytest.mark.proves("WR-EVID-11", "WR-EVID-11:no-later-row-lost", "core", "core", "must", "CI")
def test_target_no_valid_row_lost(tmp_path: Path) -> None:
    path = _plant(tmp_path)
    target_check(
        read_ndjson(path) == [FIRST, SECOND, THIRD],
        "G-D2",
        f"rows readable after a newline-less tail: {read_ndjson(path)!r}",
    )
