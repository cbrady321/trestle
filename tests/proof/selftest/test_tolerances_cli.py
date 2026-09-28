"""Self-tests for `tests/proof/tolerances.py` (L.P0-0a.4)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.proof import tolerances

ROOT = Path(__file__).resolve().parents[3]


def test_list_s0_literal_sites_exits_0_iff_site_remains() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "tests.proof.tolerances", "--list-s0-literal-sites"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "trestle/server/conductor.py" in proc.stdout


def test_conductor_literal_sites_found_at_known_lines() -> None:
    sites = tolerances.find_s0_literal_sites()
    files_lines = {(f, ln) for f, ln in sites}
    assert ("trestle/server/conductor.py", 62) in files_lines
    assert ("trestle/server/conductor.py", 65) in files_lines


def test_grace_and_kill_resolve_from_s0_runs_module() -> None:
    from trestle.server.runs import CANCEL_GRACE_S, CANCEL_KILL_S

    assert tolerances.grace() == CANCEL_GRACE_S
    assert tolerances.kill() == CANCEL_KILL_S


def test_unpublished_bound_raises_on_read() -> None:
    with pytest.raises(tolerances.ToleranceUnpublished):
        tolerances.deadline_ceiling()


def test_published_in_clock_module_is_picked_up_without_editing_tolerances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys as sys_mod
    import types

    fake_clock = types.ModuleType("trestle.common.clock")
    fake_clock.deadline_ceiling = 42.0  # type: ignore[attr-defined]
    fake_clock.stop_bound = 7.0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys_mod.modules, "trestle.common.clock", fake_clock)

    assert tolerances.deadline_ceiling() == 42.0
    assert tolerances.stop_bound() == 7.0
