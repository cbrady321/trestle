"""G-D5 (BFD-33, K-17): artifact count/bytes limits are unenforced (every
over-cap output is promoted, with no marker), and an artifact staged through
`ctx.artifact()` but never attached is silently left out of the evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, records
from tests.proof.markers import TargetUnmet, target_check
from trestle.common.limits import CaptureLimits

PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"
STAGED_NAME = "staged-report.bin"


def _cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """Run children under the S0 test limits (artifact cap = 10)."""
    monkeypatch.setenv("TRESTLE_TEST_LIMITS", "1")
    return CaptureLimits.from_env().max_artifact_count


def _run(plugin: str, args: dict[str, Any]) -> Path:
    kernel = harness.fresh_kernel([PLUGIN_DIR])
    return harness.run_to_dir(kernel, plugin, args)


def _artifact_rows(run_dir: Path) -> list[dict[str, Any]]:
    return [r for r in records.ledger_rows(run_dir).rows if r.get("kind") == "artifact_available"]


def _markers(run_dir: Path) -> list[dict[str, Any]]:
    """Every limit marker the run recorded, in the ledger's limit_exceeded
    rows."""
    out: list[dict[str, Any]] = []
    for row in records.ledger_rows(run_dir).rows:
        if row.get("kind") == "limit_exceeded":
            out.extend(m for m in row.get("markers", []) if isinstance(m, dict))
    return out


def _artifact_marker(run_dir: Path) -> bool:
    return any(
        "artifact" in f"{m.get('stream', '')} {m.get('limit', '')}" for m in _markers(run_dir)
    )


@pytest.mark.pin("G-D5")
def test_pin_over_cap_all_promoted_no_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _cap(monkeypatch)
    count = cap + 5
    run_dir = _run("many_outputs", {"count": count})
    assert len(_artifact_rows(run_dir)) == count
    assert not _artifact_marker(run_dir)


@pytest.mark.pin("G-D5")
def test_pin_staged_partial_vanishes(monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch)
    run_dir = _run("staged_only", {})
    assert _artifact_rows(run_dir) == []
    assert not _artifact_marker(run_dir)
    artifacts = run_dir / "evidence" / "artifacts"
    assert not artifacts.exists() or list(artifacts.iterdir()) == []


@pytest.mark.target("G-D5")
@pytest.mark.proves("WR-EVID-6", "WR-EVID-6:artifact-limits", "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, raises=TargetUnmet, reason="defect:G-D5")
def test_target_cap_marked_and_staged_promoted_or_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = _cap(monkeypatch)
    over = _run("many_outputs", {"count": cap + 5})
    target_check(
        _artifact_marker(over),
        "G-D5",
        f"{cap + 5} artifacts against a cap of {cap}: no artifact limit marker recorded",
    )
    staged = _run("staged_only", {})
    promoted = any(r.get("name") == STAGED_NAME for r in _artifact_rows(staged))
    target_check(
        promoted or _artifact_marker(staged),
        "G-D5",
        "a staged artifact was neither promoted nor refused with a marker",
    )
