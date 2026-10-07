"""Selftest for `python -m tests.proof.transcribe` (L.P0-0c.2): the
`--check matrix|rows|all` exit-status contract, off-HOST."""

from __future__ import annotations

from argparse import Namespace

import pytest

from tests.proof import transcribe


@pytest.mark.parametrize("mode", ["matrix", "rows"])
def test_check_modes_exit_status(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Off-HOST (no TRESTLE_REQUIREMENTS/TRESTLE_DECOMPOSITION set), both
    modes print a HOST-only notice and exit 0 (UNPROVEN, not a tool
    failure)."""
    monkeypatch.delenv("TRESTLE_REQUIREMENTS", raising=False)
    monkeypatch.delenv("TRESTLE_DECOMPOSITION", raising=False)
    args = Namespace(requirements=None, decomposition=None)
    if mode == "matrix":
        assert transcribe.cmd_check_matrix(args) == 0
    else:
        assert transcribe.cmd_check_rows(args) == 0


def test_check_all_runs_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRESTLE_REQUIREMENTS", raising=False)
    monkeypatch.delenv("TRESTLE_DECOMPOSITION", raising=False)
    args = Namespace(requirements=None, decomposition=None)
    assert transcribe.cmd_check_all(args) == 0
