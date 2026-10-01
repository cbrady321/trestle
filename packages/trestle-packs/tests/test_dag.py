"""Tests for wave planning."""

from __future__ import annotations

import pytest

from trestle_packs.core.dag import CycleError, plan_waves


def test_explicit_waves() -> None:
    plan = plan_waves(
        ["postgres", "redis", "api"],
        explicit_waves=[["postgres", "redis"], ["api"]],
        depends_on={"api": ["postgres", "redis"]},
    )
    assert plan.waves == (("postgres", "redis"), ("api",))


def test_kahn_waves() -> None:
    plan = plan_waves(
        ["a", "b", "c"],
        depends_on={"b": ["a"], "c": ["b"]},
    )
    assert plan.waves == (("a",), ("b",), ("c",))


def test_cycle_raises() -> None:
    with pytest.raises(CycleError):
        plan_waves(["a", "b"], depends_on={"a": ["b"], "b": ["a"]})


def test_forward_dependency_in_explicit_wave_raises() -> None:
    with pytest.raises(ValueError, match="prior wave"):
        plan_waves(
            ["a", "b"],
            explicit_waves=[["b"], ["a"]],
            depends_on={"b": ["a"]},
        )


def test_explicit_wave_unknown_service_refused() -> None:
    with pytest.raises(ValueError, match="unknown service in wave: ghost"):
        plan_waves(["db", "web"], explicit_waves=[["db", "ghost"], ["web"]])


def test_duplicate_service_across_waves_refused() -> None:
    with pytest.raises(ValueError, match="duplicate service in waves: db"):
        plan_waves(["db", "web"], explicit_waves=[["db"], ["db", "web"]])
    with pytest.raises(ValueError, match="duplicate service in waves: db"):
        plan_waves(["db", "web"], explicit_waves=[["db", "db"], ["web"]])


def test_sorted_order_and_leftover_wave_kept() -> None:
    plan = plan_waves(["a", "b", "c", "d"], explicit_waves=[["c", "a"]])
    assert plan.waves == (("a", "c"), ("b", "d"))
