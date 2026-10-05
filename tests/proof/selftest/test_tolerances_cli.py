"""Self-tests for `tests/proof/tolerances.py` (L.P0-0a.4)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.proof import tolerances

ROOT = Path(__file__).resolve().parents[3]


def test_list_s0_literal_sites_exits_nonzero_once_both_stop_sites_read_clock() -> None:
    """CS-2 (L.CS-2.2) moved both `grace_s=`/`kill_s=` literals in the conductor onto clock.py."""
    proc = subprocess.run(
        [sys.executable, "-m", "tests.proof.tolerances", "--list-s0-literal-sites"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == ""


def test_no_literal_site_remains_in_the_conductor() -> None:
    assert tolerances.find_s0_literal_sites() == []


def test_grace_and_kill_resolve_from_clock_and_s0_names_read_through() -> None:
    from trestle.common import clock
    from trestle.server.runs import CANCEL_GRACE_S, CANCEL_KILL_S

    assert tolerances.grace() == clock.grace == CANCEL_GRACE_S
    assert tolerances.kill() == clock.kill == CANCEL_KILL_S


def test_unpublished_bound_raises_on_read() -> None:
    # every MC-09 name is published since L.SV-3.4 (`release_slice`, `FINALIZATION_RESERVE_S`,
    # `sweep_parallelism`); a name nothing publishes raises
    with pytest.raises(tolerances.ToleranceUnpublished):
        tolerances._resolve("a_bound_nothing_publishes")  # noqa: SLF001
    from trestle.common import clock

    assert tolerances.sweep_parallelism() == float(clock.sweep_parallelism)


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
