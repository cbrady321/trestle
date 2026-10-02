"""Self-tests for `tests.proof.records` — a planted defect must be
reported, never dropped (L.P0-0b.1)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.proof import records
from trestle.server.ledger import evidence_dir, ledger_path


def _plant(run_dir: Path, raw: str) -> None:
    path = ledger_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")


def test_planted_merged_line_reported_not_dropped(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    first = json.dumps({"seq": 1, "kind": "created"})
    second = json.dumps({"seq": 2, "kind": "admitted"})
    # two records folded onto one physical line, no separating newline —
    # the product writer never does this (R-STORE-9 fsyncs a newline per
    # append); a planted fixture proves the oracle notices rather than
    # silently keeping only one.
    _plant(run_dir, first + second + "\n")

    result = records.ledger_rows(run_dir)
    assert result.merged is True
    assert result.torn is False
    assert [row["kind"] for row in result.rows] == ["created", "admitted"]


def test_planted_torn_tail_reported(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    first = json.dumps({"seq": 1, "kind": "created"})
    torn_tail = '{"seq": 2, "kind": "adm'  # interrupted mid-write, no newline
    _plant(run_dir, first + "\n" + torn_tail)

    result = records.ledger_rows(run_dir)
    assert result.torn is True
    # the well-formed row ahead of the torn tail is reported, not dropped
    assert [row["kind"] for row in result.rows] == ["created"]


def test_evidence_dir_still_holds_the_ledger(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert ledger_path(run_dir).parent == evidence_dir(run_dir)
