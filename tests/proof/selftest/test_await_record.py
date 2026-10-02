"""Selftest for `records.await_record` and its shapes: the wait returns on the condition, on the
root's terminal row, or at its bound, says which, and a torn last line is "not yet"."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from tests.proof import records, tolerances

FOSSIL = (
    Path(__file__).resolve().parents[2]
    / "fixtures/fossils/tree-trl/trl-terminal-exception_branch/home/runs/2026-09"
    / "r_aaaadihrynbukesypuo/evidence"
)


def _lane_lines() -> list[str]:
    return (FOSSIL / "lane.ndjson").read_text(encoding="utf-8").splitlines(keepends=True)


def _ledger_lines() -> list[str]:
    return (FOSSIL / "ledger.ndjson").read_text(encoding="utf-8").splitlines(keepends=True)


def _planted(tmp_path: Path) -> Path:
    (tmp_path / "evidence").mkdir()
    return tmp_path


def _write_slowly(path: Path, lines: list[str]) -> threading.Thread:
    """Append `lines` one at a time from a thread, each line in two writes (a torn line between)."""

    def write() -> None:
        with path.open("a", encoding="utf-8") as out:
            for line in lines:
                half = len(line) // 2
                out.write(line[:half])
                out.flush()
                time.sleep(tolerances.POLL_FINE_S)
                out.write(line[half:])
                out.flush()

    thread = threading.Thread(target=write)
    thread.start()
    return thread


def test_condition_when_the_awaited_end_appears(tmp_path: Path) -> None:
    run_dir = _planted(tmp_path)
    lane = _lane_lines()
    upto = next(i for i, line in enumerate(lane) if '"class":"end"' in line and '"raiser"' in line)
    writer = _write_slowly(run_dir / "evidence" / "lane.ndjson", lane[: upto + 1])
    awaited = records.await_node_end(run_dir, "raiser")
    writer.join(tolerances.JOIN_WAIT_S)
    assert awaited.why == "condition"
    assert any(r.cls == "end" and r.path == "raiser" for r in awaited.lane.rows)


def test_terminal_when_the_ledger_ends_first(tmp_path: Path) -> None:
    run_dir = _planted(tmp_path)
    (run_dir / "evidence" / "lane.ndjson").write_text("".join(_lane_lines()), encoding="utf-8")
    (run_dir / "evidence" / "ledger.ndjson").write_text("".join(_ledger_lines()), encoding="utf-8")
    awaited = records.await_node_end(run_dir, "no_such_node")
    assert awaited.why == "terminal"
    assert awaited.node.terminal == "succeeded"


def test_condition_wins_over_terminal_on_the_same_read(tmp_path: Path) -> None:
    run_dir = _planted(tmp_path)
    (run_dir / "evidence" / "lane.ndjson").write_text("".join(_lane_lines()), encoding="utf-8")
    (run_dir / "evidence" / "ledger.ndjson").write_text("".join(_ledger_lines()), encoding="utf-8")
    assert records.await_node_end(run_dir, "raiser", "sibling_b").why == "condition"


def test_timed_out_on_an_empty_run(tmp_path: Path) -> None:
    awaited = records.await_record(
        _planted(tmp_path), lambda lane, _: bool(lane.rows), bound_s=tolerances.POLL_S
    )
    assert awaited.why == "timed_out"
    assert awaited.lane.rows == []


def test_a_torn_last_line_is_not_yet(tmp_path: Path) -> None:
    run_dir = _planted(tmp_path)
    lane = _lane_lines()
    torn = lane[5][: len(lane[5]) // 2]
    (run_dir / "evidence" / "lane.ndjson").write_text("".join(lane[:5]) + torn, encoding="utf-8")
    awaited = records.await_confirmations(
        run_dir, "up", "sibling_a", "sibling_b", bound_s=tolerances.POLL_S
    )
    assert awaited.why == "condition"
    assert awaited.lane.torn
    late = records.await_node_end(run_dir, "raiser", bound_s=tolerances.POLL_S)
    assert late.why == "timed_out"
    assert late.lane.torn
