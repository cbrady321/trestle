"""CL-PX2 (L.CL-PX2.1): a run hidden by the recency cap or the scan budget is marked, not unknown.

The marker is the existing ``truncated`` field, and a run-scoped view of a run the loaded
window does not hold answers ``projection.outside_window`` (BFD-38, UAC-2); an id with no
run directory keeps ``projection.invalid_handle``. Guidance text follows (UAC-3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.limits import CaptureLimits
from trestle.common.types import RequestOutcome
from trestle.query import catalog
from trestle.query import views as view_defs
from trestle.query.fs import FilesystemQueryBackend
from trestle.server.ledger import RunLedger, ledger_path

REPO_ROOT = Path(__file__).resolve().parents[3]
MONTH = "2026-01"
CAP = view_defs.RECENCY_CACHE_SIZE


def _seed_runs(home: Path, count: int) -> list[str]:
    """Create ``count`` minimal runs; ids sort oldest to newest."""
    ids: list[str] = []
    for index in range(count):
        run_id = f"run_{index:06d}"
        run_dir = home / "runs" / MONTH / run_id
        ledger = RunLedger.open(ledger_path(run_dir))
        ledger.append("created", run_id=run_id, spec_hash="h", plugin="echo", version="0.1.0")
        ids.append(run_id)
    return ids


def _pages(backend: FilesystemQueryBackend, view: str) -> list[dict[str, object]]:
    pages: list[dict[str, object]] = []
    cursor = None
    while True:
        page = backend.query(view, {}, cursor=cursor)
        assert isinstance(page, dict)
        pages.append(page)
        cursor = page["next_cursor"]  # type: ignore[assignment]
        if cursor is None:
            return pages


def _row_count(pages: list[dict[str, object]]) -> int:
    return sum(len(page["items"]) for page in pages)  # type: ignore[arg-type]


@pytest.mark.proves("WR-EVID-7", "WR-EVID-7:window-hidden-run-marked", "core", "core", "PROC", "CI")
def test_run_beyond_recency_window_marked(tmp_path: Path) -> None:
    ids = _seed_runs(tmp_path, CAP + 1)
    backend = FilesystemQueryBackend(tmp_path)

    pages = _pages(backend, "recent_runs")
    assert _row_count(pages) == CAP
    last = pages[-1]
    assert last["next_cursor"] is None
    assert last["truncated"] is True

    hidden = ids[0]
    listed = {row["run_id"] for page in pages for row in page["items"]}  # type: ignore[union-attr]
    assert hidden not in listed
    for view in ("run", "run_provenance"):
        out = backend.query(view, {"run_id": hidden})
        assert isinstance(out, RequestOutcome), view
        assert out.code == codes.OUTSIDE_WINDOW == "projection.outside_window"
        assert out.code != codes.INVALID_HANDLE
        assert "unknown run" not in out.message

    newest = backend.query("run", {"run_id": ids[-1]})
    assert isinstance(newest, dict)
    assert newest["items"][0]["run_id"] == ids[-1]


def test_recency_cap_filled_exactly_is_not_marked(tmp_path: Path) -> None:
    _seed_runs(tmp_path, CAP)
    backend = FilesystemQueryBackend(tmp_path)

    pages = _pages(backend, "recent_runs")
    assert _row_count(pages) == CAP
    assert pages[-1]["truncated"] is False


@pytest.mark.proves("WR-EVID-7", "WR-EVID-7:window-hidden-run-marked", "core", "core", "PROC", "CI")
def test_run_hidden_by_scan_budget_marked(tmp_path: Path) -> None:
    ids = _seed_runs(tmp_path, 6)
    one_ledger = ledger_path(tmp_path / "runs" / MONTH / ids[0]).stat().st_size
    budget = CaptureLimits(max_scan_bytes=one_ledger * 3 + 1)
    backend = FilesystemQueryBackend(tmp_path, limits=budget)

    pages = _pages(backend, "recent_runs")
    assert _row_count(pages) == 3
    assert pages[-1]["truncated"] is True
    assert pages[-1]["next_cursor"] is None

    out = backend.query("run", {"run_id": ids[0]})
    assert isinstance(out, RequestOutcome)
    assert out.code == codes.OUTSIDE_WINDOW
    loaded = backend.query("run", {"run_id": ids[-1]})
    assert isinstance(loaded, dict)


@pytest.mark.proves("WR-EVID-7", "WR-EVID-7:window-hidden-run-marked", "core", "core", "PROC", "CI")
def test_truly_unknown_run_still_invalid_handle(tmp_path: Path) -> None:
    _seed_runs(tmp_path, CAP + 1)
    backend = FilesystemQueryBackend(tmp_path)

    for run_id in ("run_absent", "..", "../runs", f"{MONTH}/run_000000"):
        out = backend.query("run", {"run_id": run_id})
        assert isinstance(out, RequestOutcome), run_id
        assert out.code == codes.INVALID_HANDLE, run_id

    complete = tmp_path / "small"
    _seed_runs(complete, 2)
    small = FilesystemQueryBackend(complete)
    out = small.query("run", {"run_id": "run_absent"})
    assert isinstance(out, RequestOutcome)
    assert out.code == codes.INVALID_HANDLE


@pytest.mark.proves("WR-EVID-7", "WR-EVID-7:window-hidden-run-marked", "core", "core", "PROC", "CI")
def test_cap_case_guidance_corrected() -> None:
    text = (REPO_ROOT / "docs" / "agents.md").read_text(encoding="utf-8")
    rule = next(line for line in text.splitlines() if line.startswith("5. **Fetch truncation"))
    # Following the cursor is conditioned on it being set; the null case names outside_window.
    assert "`next_cursor` set means follow the cursor" in rule
    assert "`next_cursor` null" in rule
    assert "`projection.outside_window`" in rule

    entry = next(row for row in catalog.view_catalog()["views"] if row["name"] == "recent_runs")
    when = entry["use_when"]
    assert "Follow next_cursor when truncated" not in when
    assert "next_cursor when it is set" in when
    assert "next_cursor null" in when
    assert "projection.outside_window" in when
