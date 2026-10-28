"""G-D5 (BFD-33, K-17), flipped by L.CL-B1.3: artifact count and byte limits used to be
unenforced (every over-cap output was promoted, with no marker), and an artifact staged through
`ctx.artifact()` but never attached was silently left out of the evidence. Both caps now hold on
every path, and a staged artifact is promoted or refused with a marker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, records
from tests.proof.markers import target_check
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


@pytest.mark.proves("WR-EVID-6", "WR-EVID-6:artifact-limits", "core", "core", "must", "CI")
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
