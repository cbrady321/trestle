"""Selftest for `ckpt core` condition (d)'s gap-entry run (L.P0-0d.22).

A gap's gaps.toml `entry` is its entry point: a test, or (lane C) a helper its module's tests
call, e.g. `tests.pins.c_surface.test_g_c1:wait_at_budget`. Only collected node ids may reach
pytest: one unknown id makes pytest refuse the whole `--runxfail` run, which read every core gap
as "no result". Synthetic gaps, collections and audits only; no live condition runs here.
"""

from __future__ import annotations

from typing import Any

from tests.proof.ckpt import core

C1 = "tests/pins/c_surface/test_g_c1.py"
A1 = "tests/pins/a_lifecycle/test_g_a1.py::test_target_a1"
COLLECTED = [
    A1,
    f"{C1}::test_target_answer_terminal",
    f"{C1}::test_pin_budget[2]",
    "tests/pins/c_surface/test_g_c2.py::test_target_c2",
]
GAPS = [
    {"id": "G-A1", "entry": "tests.pins.a_lifecycle.test_g_a1:test_target_a1"},
    {"id": "G-C1", "entry": "tests.pins.c_surface.test_g_c1:wait_at_budget"},  # a helper
    {"id": "G-E1", "entry": "tests.pins.e.test_g_e1:test_not_core"},  # not a core gap
    {"id": "G-D1", "entry": "pending"},
]


def _pytest_like(outcome_of: dict[str, str] | None = None):
    """An audit that refuses the whole run on an uncollected id, as pytest does."""
    seen: list[list[str]] = []

    def audit(args: list[str]) -> dict[str, Any]:
        seen.append(args)
        assert args[0] == "--runxfail"
        if not set(args[1:]) <= set(COLLECTED):
            return {"nodes": [], "outcomes": {}}
        return {"outcomes": {n: {"outcome": (outcome_of or {}).get(n, "passed")} for n in args[1:]}}

    return audit, seen


def test_helper_entry_reaches_pytest_as_its_module_tests_and_every_gap_passes() -> None:
    audit, seen = _pytest_like()
    got = core.gap_entry_outcomes(GAPS, COLLECTED, audit)
    assert got == {"G-A1": "passed", "G-C1": "passed"}
    assert seen == [
        ["--runxfail", A1, f"{C1}::test_pin_budget[2]", f"{C1}::test_target_answer_terminal"]
    ]
    world = core.World(gap_entries={gap: "passed" for gap in core.CORE_GAPS} | got)
    assert core.verdict_d(world) == (True, "")


def test_helper_entry_fails_when_one_module_test_fails() -> None:
    audit, _seen = _pytest_like({f"{C1}::test_pin_budget[2]": "failed"})
    got = core.gap_entry_outcomes(GAPS, COLLECTED, audit)
    assert got["G-C1"] == "failed"
    world = core.World(gap_entries={gap: "passed" for gap in core.CORE_GAPS} | got)
    ok, reason = core.verdict_d(world)
    assert not ok and "G-C1" in reason


def test_uncollected_entry_is_no_result_and_never_reaches_pytest() -> None:
    audit, seen = _pytest_like()
    got = core.gap_entry_outcomes(GAPS, COLLECTED[:1], audit)
    assert got == {"G-A1": "passed", "G-C1": "no result"}
    assert seen == [["--runxfail", A1]]


def test_entry_node_ids_keep_parametrized_ids_of_a_test_entry() -> None:
    collected = [f"{C1}::test_x[1]", f"{C1}::test_x[2]", f"{C1}::test_y"]
    assert core.gap_entry_node_ids("tests.pins.c_surface.test_g_c1:test_x", collected) == [
        f"{C1}::test_x[1]",
        f"{C1}::test_x[2]",
    ]
