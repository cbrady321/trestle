"""CS-1 framing (L.CS-1.1): a valid newline-less tail is terminated, a torn tail truncated,
neither merged. Append cost independent of history and its published ratio (L.CS-1.2)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.pins.d_evidence._bytes_read import count_bytes_read
from tests.proof import records, tolerances
from trestle.child.context import RuntimeContext
from trestle.common.fsutil import _TAIL_WINDOW, append_ndjson, read_ndjson
from trestle.common.limits import CaptureLimits
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.recovery import recover_run_dir

REPO_ROOT = Path(__file__).resolve().parents[3]

ROW_A = {"kind": "a", "n": 1}
ROW_B = {"kind": "b", "n": 2}
ROW_C = {"kind": "c", "n": 3}
ROW_D = {"kind": "d", "n": 4}


def _encode(row: dict[str, object]) -> bytes:
    return json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(run_dir: Path) -> dict[str, Path]:
    """The two ndjson files a run keeps: the ledger and the child's events."""
    events = evidence_dir(run_dir) / "events.ndjson"
    return {"ledger.ndjson": ledger_path(run_dir), "events.ndjson": events}


def _strict_rows(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    """An independent oracle (not `read_ndjson`): every physical line of the file, byte for byte.
    Returns the rows and the problems (a line that is not exactly one object, a missing final
    newline)."""
    data = path.read_bytes()
    problems = [] if data.endswith(b"\n") or not data else ["final line has no newline"]
    rows: list[dict[str, object]] = []
    for line in data.split(b"\n")[: -1 if data.endswith(b"\n") else None]:
        try:
            row = json.loads(line.decode("utf-8"))
        except ValueError:
            problems.append(f"unparseable line {line[:40]!r}")
            continue
        if not isinstance(row, dict):
            problems.append(f"line is not one object: {line[:40]!r}")
        else:
            rows.append(row)
    return rows, problems


def _plant(path: Path, rows: list[dict[str, object]], tail: bytes = b"") -> bytes:
    """`rows`, each newline-terminated, then `tail` as it is; returns the bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(_encode(r) + b"\n" for r in rows) + tail
    path.write_bytes(data)
    return data


def _assert_read_everywhere(path: Path, run_dir: Path, expected: list[dict[str, object]]) -> None:
    assert read_ndjson(path) == expected
    rows, problems = _strict_rows(path)
    assert rows == expected and problems == []
    if path == ledger_path(run_dir):  # MC-10: the strict oracle over the ledger
        oracle = records.ledger_rows(run_dir)
        assert oracle.rows == expected and not oracle.merged and not oracle.torn


@pytest.mark.proves("WR-EVID-11", "A9.4", "core", "core", "must", "CI")
@pytest.mark.proves(
    "WR-EVID-11", "WR-EVID-11:newline-less-tail-terminated", "core", "core", "LOGIC", "CI"
)
@pytest.mark.proves("WR-EVID-11", "WR-EVID-11:no-later-row-lost", "core", "core", "must", "CI")
def test_newline_less_tail_terminated_no_valid_row_lost(tmp_path: Path) -> None:
    for name in ("ledger.ndjson", "events.ndjson"):
        run_dir = tmp_path / name.split(".")[0]
        path = _files(run_dir)[name]

        # row A written without its trailing newline, then B and every later row
        committed = _plant(path, [], tail=_encode(ROW_A))
        append_ndjson(path, ROW_B)
        append_ndjson(path, ROW_C)
        _assert_read_everywhere(path, run_dir, [ROW_A, ROW_B, ROW_C])
        # no committed byte was rewritten: the file still begins with them, unchanged
        assert _sha(path.read_bytes()[: len(committed)]) == _sha(committed)
        assert path.read_bytes()[len(committed) : len(committed) + 1] == b"\n"

        # a valid unterminated tail after ordinary rows: the prefix's sha256 is unchanged
        path.unlink()
        committed = _plant(path, [ROW_A, ROW_B], tail=_encode(ROW_C))
        append_ndjson(path, ROW_D)
        _assert_read_everywhere(path, run_dir, [ROW_A, ROW_B, ROW_C, ROW_D])
        assert _sha(path.read_bytes()[: len(committed)]) == _sha(committed)

        # an unparseable partial tail is truncated at the last newline; the rows before it stay
        path.unlink()
        committed = _plant(path, [ROW_A, ROW_B], tail=b'{"kind":"c","n"')
        good = committed[: committed.rindex(b"\n") + 1]
        append_ndjson(path, ROW_C)
        _assert_read_everywhere(path, run_dir, [ROW_A, ROW_B, ROW_C])
        assert _sha(path.read_bytes()[: len(good)]) == _sha(good)

        # a torn line that did end in its newline, over a valid unterminated row: only the torn
        # line goes
        path.unlink()
        _plant(path, [ROW_A], tail=_encode(ROW_B) + b'\n{"kind":"broken"\n')
        append_ndjson(path, ROW_C)
        _assert_read_everywhere(path, run_dir, [ROW_A, ROW_B, ROW_C])

        # a file with nothing valid left restarts clean; an empty file and an absent one append
        path.unlink()
        path.write_bytes(b'{"kind":"tor')
        append_ndjson(path, ROW_A)
        _assert_read_everywhere(path, run_dir, [ROW_A])
        path.unlink()
        path.write_bytes(b"")
        append_ndjson(path, ROW_A)
        path.unlink()
        append_ndjson(path, ROW_A)
        _assert_read_everywhere(path, run_dir, [ROW_A])

        # a well-formed file reads identically and its append leaves every byte in place
        path.unlink()
        committed = _plant(path, [ROW_A, ROW_B])
        append_ndjson(path, ROW_C)
        _assert_read_everywhere(path, run_dir, [ROW_A, ROW_B, ROW_C])
        assert path.read_bytes().startswith(committed)

    # read_ndjson: a valid unterminated final line is a row; an unparseable fragment is skipped
    path = tmp_path / "read.ndjson"
    _plant(path, [ROW_A], tail=_encode(ROW_B))
    assert read_ndjson(path) == [ROW_A, ROW_B]
    _plant(path, [ROW_A], tail=b'{"kind":"b","n"')
    assert read_ndjson(path) == [ROW_A]


def test_terminal_row_without_newline_recovers_as_terminal(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "2099-01" / "r_cs1_terminal"
    evidence_dir(run_dir).mkdir(parents=True)
    ledger = RunLedger.open(ledger_path(run_dir))
    run_id = "r_cs1_terminal"
    for kind, fields in (
        ("created", {"spec_hash": "t", "plugin": "echo", "version": "0.1.0"}),
        ("admitted", {"snapshot_id": "snap_t"}),
        ("started", {}),
        ("execution_ended", {"classification": "succeeded", "exit_code": 0, "duration_ms": 1}),
        ("evidence_finalized", {"completeness": "complete", "result_state": "absent"}),
    ):
        ledger.append(kind, run_id=run_id, **fields)
    # the terminal row is written whole but its trailing newline never landed
    with ledger_path(run_dir).open("ab") as fh:
        fh.write(_encode({"seq": 6, "kind": "succeeded", "run_id": run_id}))
    before = ledger_path(run_dir).read_bytes()

    recover_run_dir(run_dir)

    reopened = RunLedger.open(ledger_path(run_dir))
    assert reopened.terminal_state() == "succeeded"
    assert not reopened.has_kind("interrupted")
    assert ledger_path(run_dir).read_bytes() == before  # recovery appended nothing
    meta = json.loads((evidence_dir(run_dir) / "meta.json").read_text(encoding="utf-8"))
    assert meta["classification"] == "succeeded"

    # a run that lost more than the newline is interrupted, and its own suffix rows are readable
    torn_dir = tmp_path / "runs" / "2099-01" / "r_cs1_torn"
    evidence_dir(torn_dir).mkdir(parents=True)
    torn = RunLedger.open(ledger_path(torn_dir))
    torn.append("created", run_id="r_cs1_torn", spec_hash="t", plugin="echo", version="0.1.0")
    with ledger_path(torn_dir).open("ab") as fh:
        fh.write(_encode({"seq": 2, "kind": "started", "run_id": "r_cs1_torn"}))  # no newline
    recover_run_dir(torn_dir)
    kinds = [row["kind"] for row in read_ndjson(ledger_path(torn_dir))]
    assert kinds == ["created", "started", "evidence_finalized", "interrupted"]
    assert records.ledger_rows(torn_dir).merged is False


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-18", "core", "core", "INSPECT", "CI")
def test_k18_documented() -> None:
    doc = (REPO_ROOT / "docs" / "operator-sessions-telemetry.md").read_text(encoding="utf-8")
    assert "(K-18)" in doc and "newline-less tail is terminated" in doc


EVENTS = 4000
QUARTER = EVENTS // 4
SMALL_FILE_BYTES = 1024
LARGE_FILE_BYTES = 8 * 1024 * 1024


def _filler_file(path: Path, size: int) -> None:
    """Newline-terminated valid rows totalling at least `size` bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = _encode({"kind": "log", "pad": "x" * 100}) + b"\n"
    path.write_bytes(row * (size // len(row) + 1))


def _bytes_read_by_append(path: Path) -> int:
    with count_bytes_read(path) as counter:
        append_ndjson(path, ROW_A)
    return counter.bytes_read


@pytest.mark.proves("WR-EVID-4", "A9.2", "core", "core", "must", "CI")
@pytest.mark.proves("WR-EVID-4", "WR-EVID-4:flat-append", "core", "core", "must", "CI")
def test_e6_rerun_quartile_ratio(tmp_path: Path) -> None:
    work, evidence = tmp_path / "work", tmp_path / "evidence"
    evidence.mkdir()
    limits = dataclasses.replace(CaptureLimits(), max_events_per_second=EVENTS * 2)
    ctx = RuntimeContext(
        work=work,
        evidence=evidence,
        deadline=datetime.now(UTC),
        events_path=evidence / "events.ndjson",
        limits=limits,
    )
    elapsed: list[float] = []
    for i in range(EVENTS):
        started = time.perf_counter()
        ctx._emit("log", {"message": f"event {i}"})
        elapsed.append(time.perf_counter() - started)
    assert len(read_ndjson(evidence / "events.ndjson")) == EVENTS  # every event recorded

    first = QUARTER / sum(elapsed[:QUARTER])
    last = QUARTER / sum(elapsed[-QUARTER:])
    assert last / first >= tolerances.append_cost_ratio(), (first, last)

    # the bytes one append reads are the same (within one window) at 1 KiB and at 8 MiB
    _filler_file(tmp_path / "small" / "ledger.ndjson", SMALL_FILE_BYTES)
    small = _bytes_read_by_append(tmp_path / "small" / "ledger.ndjson")
    _filler_file(tmp_path / "large" / "ledger.ndjson", LARGE_FILE_BYTES)
    large = _bytes_read_by_append(tmp_path / "large" / "ledger.ndjson")
    assert 0 < small <= large <= small + _TAIL_WINDOW
    assert large <= _TAIL_WINDOW
    assert (tmp_path / "large" / "ledger.ndjson").stat().st_size >= LARGE_FILE_BYTES
