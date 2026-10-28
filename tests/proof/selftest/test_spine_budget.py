"""Selftest for the spine-budget check (L.CZ.8; J-ROOT step 3). Synthetic junit only: the live
check runs against the CZ PR's spine junit and is accepted there."""

from __future__ import annotations

from pathlib import Path

from tests.proof.selftest import spine_budget_check as budget_mod


def _junit(tmp_path: Path, body: str, name: str = "junit.xml") -> Path:
    path = tmp_path / name
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?>{body}')
    return path


def test_planted_slow_junit_fails(tmp_path: Path) -> None:
    slow = budget_mod.SPINE_BUDGET_S + 1
    over = _junit(tmp_path, f'<testsuites><testsuite name="pytest" time="{slow}"/></testsuites>')
    ok, message = budget_mod.check_spine_gate_within_budget(over)
    assert not ok and "over the" in message
    assert budget_mod.main(["--junit", str(over)]) == 1

    # the same suite one second inside the budget passes, through both the function and the CLI
    inside = budget_mod.SPINE_BUDGET_S - 1
    within = _junit(
        tmp_path, f'<testsuites><testsuite name="pytest" time="{inside}"/></testsuites>', "in.xml"
    )
    assert budget_mod.check_spine_gate_within_budget(within)[0]
    assert budget_mod.main(["--junit", str(within)]) == 0


def test_duration_reads_the_root_time_else_the_suites(tmp_path: Path) -> None:
    half = budget_mod.SPINE_BUDGET_S / 2
    root_time = _junit(
        tmp_path, f'<testsuites time="{half}"><testsuite name="a" time="9999"/></testsuites>'
    )
    assert budget_mod.junit_duration_s(root_time) == half
    summed = _junit(
        tmp_path,
        f'<testsuites><testsuite time="{half}"/><testsuite time="{half + 1}"/></testsuites>',
        "sum.xml",
    )
    assert budget_mod.junit_duration_s(summed) == 2 * half + 1
    assert not budget_mod.check_spine_gate_within_budget(summed)[0]
    lone = _junit(tmp_path, '<testsuite name="pytest" time="3.5"/>', "lone.xml")
    assert budget_mod.junit_duration_s(lone) == 3.5


def test_an_unreadable_or_timeless_junit_is_a_failed_check(tmp_path: Path) -> None:
    missing = tmp_path / "absent.xml"
    assert not budget_mod.check_spine_gate_within_budget(missing)[0]
    timeless = _junit(tmp_path, "<testsuites><testsuite name='x'/></testsuites>")
    ok, message = budget_mod.check_spine_gate_within_budget(timeless)
    assert not ok and "no testsuite carries a time" in message
    garbage = tmp_path / "garbage.xml"
    garbage.write_text("not xml")
    assert not budget_mod.check_spine_gate_within_budget(garbage)[0]
    assert budget_mod.main(["--junit", str(garbage)]) == 1
