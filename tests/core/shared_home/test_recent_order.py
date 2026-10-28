"""`recent_runs` lists runs newest first by the timestamp their id carries.

Run ids are `r_` + standard base32 of an 8-byte millisecond timestamp (a-z, then 2-7, in the
alphabet), so as strings the digits sort before the letters and two runs a few ms apart can sort
the wrong way round. The recency window orders on the id's timestamp, not on its text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.core.shared_home.test_run_by_key import _home, _kernel, _send
from trestle.common.ids import _base32_encode, run_id_ms, run_id_recency
from trestle.server import admission

BASE_MS = 1_790_000_000_000 & ~0xF
OLDER_MS = BASE_MS | 12  # last base32 digit of the timestamp: 'y'
NEWER_MS = BASE_MS | 13  # ... and '2', which sorts BEFORE 'y' as text


def _run_id(ms: int, rand: str = "aaaaaa") -> str:
    return f"r_{_base32_encode(ms.to_bytes(8, 'big'))}{rand}"


def test_the_straddling_ids_sort_the_wrong_way_as_strings_and_the_right_way_by_time() -> None:
    older, newer = _run_id(OLDER_MS), _run_id(NEWER_MS)
    assert older[2:15][-1] == "y" and newer[2:15][-1] == "2"
    assert newer < older  # the text order is the reverse of time
    assert run_id_ms(older) == OLDER_MS and run_id_ms(newer) == NEWER_MS
    assert sorted([older, newer], key=run_id_recency, reverse=True) == [newer, older]


def test_recent_runs_lists_runs_whose_ids_straddle_the_base32_boundary_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    kernel: Any = _kernel(home)
    ids = iter([_run_id(OLDER_MS), _run_id(NEWER_MS)])
    monkeypatch.setattr(admission, "generate_run_id", lambda: next(ids))
    older = _send(kernel, "order:old")
    newer = _send(kernel, "order:new")
    assert (older.run_id, newer.run_id) == (_run_id(OLDER_MS), _run_id(NEWER_MS))
    recent = kernel.control.query("recent_runs", {})
    assert isinstance(recent, dict)
    assert [r["run_id"] for r in recent["items"]] == [newer.run_id, older.run_id]


def test_the_window_keeps_the_newest_runs_when_the_cap_cuts_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trestle.query import views as view_defs

    home = _home(tmp_path)
    kernel: Any = _kernel(home)
    # ids a few ms apart whose text order disagrees with their time order
    stamps = [BASE_MS | n for n in (10, 11, 12, 13, 14)]
    ids = iter(_run_id(ms) for ms in stamps)
    monkeypatch.setattr(admission, "generate_run_id", lambda: next(ids))
    sent = [_send(kernel, f"order:{n}") for n in range(len(stamps))]
    monkeypatch.setattr(view_defs, "RECENCY_CACHE_SIZE", 3)
    recent = kernel.control.query("recent_runs", {})
    assert isinstance(recent, dict) and recent["truncated"] is True
    newest_three = [s.run_id for s in reversed(sent)][:3]
    assert [r["run_id"] for r in recent["items"]] == newest_three
