"""L.SV-3.7: the plan-rank release sweep (B2-C9, V-10) over the V-10 descriptor forms, against a
stub engine that records every command the sweep runs: observe, stop, remove, observe (V-10.4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from tests.proof import tolerances
from tests.single.control.sweep_stub import (
    BUDGET,
    EXE,
    LIMITS,
    NOT_APPLIED,
    STEP,
    T0,
    UNSURE,
    Engine,
    Group,
    descriptor,
    folded,
    names,
    run_sweep,
    ticket,
)
from trestle.common import lane_format as lf
from trestle.common.plan.compiler import implicit_depth1_plan
from trestle.server import sweep
from trestle.server.config import OperatorLimits, TrestleConfig
from trestle.server.ledger import RunLedger


@pytest.mark.parametrize("sa", ["SA-14"])
def test_single_vertex_reverse_issue_order(sa: str) -> None:
    engine = Engine(present={"a", "b", "c"})
    record = folded(ticket("a"), ticket("b"), ticket("c"))
    disposition = run_sweep(record, engine)
    # reverse issue order inside the node; one target's commands are together (parallelism 1)
    assert engine.calls == [
        ("obs", "c"), ("stop", "c"), ("rm", "c"), ("obs", "c"),
        ("obs", "b"), ("stop", "b"), ("rm", "b"), ("obs", "b"),
        ("obs", "a"), ("stop", "a"), ("rm", "a"), ("obs", "a"),
    ]  # fmt: skip
    assert names(disposition.released) == ["c", "b", "a", None]  # the group target closes the list


def test_targets_in_descending_release_rank() -> None:
    """Rank first (descending), reverse issue order only inside a node; every rank done before the
    next, at most `sweep_parallelism` inside one."""
    plan = replace(implicit_depth1_plan("root"), release_rank={"": 0, "lo": 0, "mid": 1, "hi": 2})
    engine = Engine(present={"x", "y", "z", "w"})
    record = folded(
        ticket("x", path=("lo",)),
        ticket("y", path=("hi",)),
        ticket("z", path=("mid",)),
        ticket("w", path=("hi",)),
    )
    disposition = run_sweep(record, engine, plan=plan)
    first_obs = [name for role, name in engine.calls if role == "obs"][::2]
    assert first_obs == ["w", "y", "z", "x"]  # rank 2 (reverse issue order), rank 1, rank 0
    assert names(disposition.released[:4]) == ["w", "y", "z", "x"]


def test_parallelism_is_bounded_and_confined_to_one_rank() -> None:
    import threading

    lock = threading.Lock()
    gate = threading.Event()
    engine = Engine(present={"a", "b", "c", "d"})
    real = engine.run

    def slow(release: lf.ArgvRelease, argv: Any, timeout_s: float) -> sweep.CommandResult:
        with lock:
            engine.active += 1
            engine.peak = max(engine.peak, engine.active)
        gate.wait(tolerances.SETTLE_SHORT_S)
        try:
            return real(release, argv, timeout_s)
        finally:
            with lock:
                engine.active -= 1

    plan = replace(implicit_depth1_plan("root"), release_rank={"": 0, "p": 1, "q": 0})
    record = folded(
        ticket("a", path=("p",)),
        ticket("b", path=("p",)),
        ticket("c", path=("p",)),
        ticket("d", path=("q",)),
    )
    limits = replace(LIMITS, sweep_parallelism=2)
    disposition = sweep.sweep(record, Group(), BUDGET, limits, plan, io=sweep.SweepIO(run=slow))
    assert engine.peak == 2  # never more than sweep_parallelism at once
    # the lower rank started only after the whole higher rank was done
    last_of_high = max(i for i, (_, n) in enumerate(engine.calls) if n in {"a", "b", "c"})
    first_of_low = min(i for i, (_, n) in enumerate(engine.calls) if n == "d")
    assert last_of_high < first_of_low
    assert len(disposition.released) == 5


def test_argv_release_outside_allowlist_unknown_never_run() -> None:
    engine = Engine(present={"a"})
    listed_elsewhere = OperatorLimits(release_executables=frozenset({"/other/ctl"}))
    empty = OperatorLimits()  # the default allowlist: empty (F-11(b))
    for limits in (listed_elsewhere, empty):
        disposition = run_sweep(folded(ticket("a")), engine, limits=limits)
        assert names(disposition.unknown) == ["a"] and disposition.released == (sweep.GROUP_TARGET,)
    assert engine.calls == []  # never run


def test_argv_release_observe_stop_remove_observe() -> None:
    engine = Engine(present={"a"})
    disposition = run_sweep(folded(ticket("a")), engine)
    assert engine.roles("a") == ["obs", "stop", "rm", "obs"]
    assert names(disposition.released) == ["a", None]
    assert not disposition.unknown
    # already absent on the first look, for a confirmed target: released, nothing else run
    quiet = Engine()
    assert names(run_sweep(folded(ticket("a")), quiet).released) == ["a", None]
    assert quiet.roles("a") == ["obs"]


def test_stopped_not_removed_stays_unknown() -> None:
    engine = Engine(present={"a"}, stop_leaves_it={"a"})
    record = folded(ticket("a", descriptor("a", remove=False)))
    disposition = run_sweep(record, engine)
    assert engine.roles("a") == ["obs", "stop", "obs"]  # a stopped, unremoved instance is present
    assert "a" in names(disposition.unknown)
    assert "a" not in names(disposition.released)


def test_failed_stop_skips_remove_unknown() -> None:
    engine = Engine(present={"a", "t"}, stop_fails={"a"}, timing_out={"t"})
    disposition = run_sweep(folded(ticket("a"), ticket("t")), engine)
    assert engine.roles("a") == ["obs", "stop"] and "rm" not in engine.roles("a")  # no retry either
    assert "a" in names(disposition.unknown)
    # a failed remove is unknown too
    engine2 = Engine(present={"r"}, remove_fails={"r"})
    disposition2 = run_sweep(folded(ticket("r")), engine2)
    assert engine2.roles("r") == ["obs", "stop", "rm"] and "r" in names(disposition2.unknown)


def test_could_not_observe_unknown() -> None:
    engine = Engine(unobservable={"u"}, timing_out={"t"}, huge_output={"h"})
    record = folded(ticket("u"), ticket("t"), ticket("h"))
    disposition = run_sweep(record, engine)
    assert set(names(disposition.unknown)) >= {"u", "t", "h"}
    assert not [c for c in engine.calls if c[0] != "obs"]  # nothing stopped what it could not see


def test_all_not_applied_nothing_created_nothing_run() -> None:
    engine = Engine(present={"a"})
    record = folded(ticket("a", confirmation=NOT_APPLIED), ticket("a", confirmation=NOT_APPLIED))
    disposition = run_sweep(record, engine)
    assert names(disposition.nothing_created) == ["a"]
    assert engine.calls == []
    # a Durable target that was never applied is nothing_created as well
    durable = ticket("d", lf.Durable(lf.DurableOwner.HOST), confirmation=NOT_APPLIED)
    assert names(run_sweep(folded(durable), engine).nothing_created) == ["d"]


def test_durable_left_durable_with_owner() -> None:
    engine = Engine()
    record = folded(
        ticket("h", lf.Durable(lf.DurableOwner.HOST)),
        ticket("e", lf.Durable(lf.DurableOwner.ENVIRONMENT)),
    )
    disposition = run_sweep(record, engine)
    assert {(t.effect, owner) for t, owner in disposition.left_durable} == {
        ("h", "host"),
        ("e", "environment"),
    }
    assert engine.calls == []
    assert names(disposition.released) == [None]  # only the group target


@pytest.mark.parametrize("sa", ["SA-14"])
def test_group_target_released_only_confirmed_and_no_helpers(sa: str) -> None:
    plain = lf.InRunGroup()
    helpers = lf.InRunGroup(helpers_disclosed=True)
    group_entry = ticket("g", plain)
    for confirmed, entries, expected in (
        (True, (), sweep.RELEASED),
        (True, (group_entry,), sweep.RELEASED),
        (False, (), sweep.UNKNOWN),
        (False, (group_entry,), sweep.UNKNOWN),
        (True, (ticket("h", helpers),), sweep.UNKNOWN),  # helpers disclosed: never released
        (True, (ticket("h", helpers, confirmation=NOT_APPLIED),), sweep.RELEASED),  # took no place
        (True, (ticket("h", helpers, released_at=T0),), sweep.RELEASED),  # recorded released
    ):
        disposition = run_sweep(folded(*entries), Engine(), group=Group(confirmed))
        assert sweep.GROUP_TARGET in (
            disposition.released if expected == sweep.RELEASED else disposition.unknown
        ), (confirmed, entries)
        # and the InRunGroup entries are the group target's: no target of their own
        assert all(t == sweep.GROUP_TARGET for t in disposition.all_targets())


def test_group_target_never_nothing_created() -> None:
    for gone in (True, False):
        for entries in ((), (ticket("g", lf.InRunGroup(), confirmation=NOT_APPLIED),)):
            disposition = run_sweep(folded(*entries), Engine(), group=Group(gone))
            assert sweep.GROUP_TARGET not in disposition.nothing_created
    # an unconfirmed InRunGroup entry does not make the group target nothing_created either
    unsure = folded(ticket("g", lf.InRunGroup(), confirmation=UNSURE))
    assert sweep.GROUP_TARGET not in run_sweep(unsure, Engine()).nothing_created
    # recovery never yields it for an unconfirmed argv target, and neither does the group
    recovered = run_sweep(folded(ticket("a", confirmation=UNSURE)), Engine(), recovery=True)
    assert sweep.GROUP_TARGET not in recovered.nothing_created


def test_unknown_paths_never_targets_cleanup_unknown() -> None:
    engine = Engine(present={"a"})
    record = folded(ticket("a"), unknown_paths=("stray/node",))
    disposition = run_sweep(record, engine)
    # the refused entry is not a target and nothing is run for it; the cleanup is unknown anyway
    assert all(t.path != ("stray", "node") for t in disposition.all_targets())
    assert [n for _, n in engine.calls if n not in {"a"}] == []
    assert not disposition.unknown  # every target here was released ...
    assert sweep.cleanup_unknown(record, disposition)  # ... and the cleanup is still unknown


@pytest.mark.proves("WR-OWN-6", "WR-OWN-6:A-unknown-never-clean", "A", "single", "PROC", "CI")
def test_unknown_never_reported_clean() -> None:
    clean = folded(ticket("a"))
    disposition = run_sweep(clean, Engine(present={"a"}))
    assert not sweep.cleanup_unknown(clean, disposition)  # the one case that may read clean
    for record, engine, group in (
        (folded(ticket("a")), Engine(unobservable={"a"}), Group()),  # an unknown target
        (folded(ticket("a")), Engine(present={"a"}), Group(False)),  # the group not confirmed gone
        (folded(ticket("a", lf.InRunGroup(True))), Engine(), Group()),  # disclosed helpers
        (folded(ticket("a"), unknown_paths=("x",)), Engine(), Group()),  # a refused entry
        (folded(ticket("a"), overflowed=True), Engine(), Group()),  # an overflowed lane
    ):
        result = run_sweep(record, engine, group=group)
        assert sweep.cleanup_unknown(record, result)


def test_overflowed_lane_cleanup_unknown_refused_full_unchanged() -> None:
    engine = Engine(present={"a"})
    overflowed = folded(ticket("a"), overflowed=True)
    disposition = run_sweep(overflowed, engine)
    assert names(disposition.released) == ["a", None]  # every target would be released ...
    assert sweep.cleanup_unknown(overflowed, disposition)  # ... yet the cleanup is unknown
    # refused_full alone changes nothing
    full = folded(ticket("a"), refused_full=True)
    same = run_sweep(full, Engine(present={"a"}))
    assert same == disposition and not sweep.cleanup_unknown(full, same)


def test_budget_exhausted_targets_unknown() -> None:
    engine = Engine(present={"a", "b"})
    record = folded(ticket("a"), ticket("b"))
    # a budget of nothing reaches no target
    none = run_sweep(record, engine, budget=timedelta(0))
    assert set(names(none.unknown)) >= {"a", "b"} and engine.calls == []
    # a budget that runs out after the first target's commands: the rest are unknown, not run
    clock = {"now": 0.0}

    def tick() -> float:
        return clock["now"]

    real = engine.run

    def run(release: lf.ArgvRelease, argv: Any, timeout_s: float) -> sweep.CommandResult:
        clock["now"] += float(BUDGET.total_seconds())  # every command uses the whole budget
        return real(release, argv, timeout_s)

    disposition = sweep.sweep(
        record, Group(), BUDGET, LIMITS, io=sweep.SweepIO(run=run, monotonic=tick)
    )
    # b (issued last, swept first) got one look, which used the whole budget: its stop was out of
    # budget and it is unknown; a was never reached, so no command ran for it
    assert engine.roles("b") == ["obs"] and engine.roles("a") == []
    assert {"a", "b"} <= set(names(disposition.unknown))


def test_recovery_never_downgrades_or_nothing_created_outside_settle() -> None:
    """Unconfirmed targets: outside recovery `nothing_created` only after the settle condition."""
    unsure = ticket("u", confirmation=UNSURE, issued_at=T0)
    after_settle = sweep.SweepIO(run=Engine().run, now=lambda: T0 + STEP + timedelta(seconds=1))
    ok = run_sweep(folded(unsure), Engine(), io=after_settle)
    assert names(ok.nothing_created) == ["u"]
    # group not confirmed gone: the settle condition cannot hold
    not_gone = run_sweep(folded(unsure), Engine(), group=Group(False), io=after_settle)
    assert names(not_gone.unknown)[0] == "u" and not not_gone.nothing_created
    # too early: wait until then (a fake sleep), look once more, then nothing_created
    slept: list[float] = []
    early = sweep.SweepIO(run=Engine().run, now=lambda: T0, sleep=slept.append)
    waited = run_sweep(folded(unsure), Engine(), io=early)
    assert slept == [STEP.total_seconds()] and names(waited.nothing_created) == ["u"]


@pytest.mark.gated_on("F-11(b)")
def test_populated_allowlist_variant() -> None:
    """The variant where the operator populated the allowlist: the listed executable is run
    (observe, stop, remove, observe) and an unlisted one is still refused."""
    engine = Engine(present={"a", "b"})
    record = folded(ticket("a"), ticket("b", descriptor("b", exe="/unlisted/ctl")))
    populated = OperatorLimits(release_executables=frozenset({EXE}), sweep_parallelism=1)

    def run(release: lf.ArgvRelease, argv: Any, timeout_s: float) -> sweep.CommandResult:
        assert release.executable == EXE, "an unlisted executable was run"
        return engine.run(release, argv, timeout_s)

    disposition = sweep.sweep(record, Group(), BUDGET, populated, io=sweep.SweepIO(run=run))
    assert engine.roles("a") == ["obs", "stop", "rm", "obs"] and "a" in names(disposition.released)
    assert "b" in names(disposition.unknown) and engine.roles("b") == []


@pytest.mark.gated_on("OQ-30")
def test_success_with_unknown_acceptable_variant() -> None:
    """A run that succeeded whose cleanup is unknown (the group cannot be confirmed) is still
    answered from the record: the sweep reports unknown and neither hides nor invents a release."""
    record = folded(ticket("a"))
    disposition = run_sweep(record, Engine(present={"a"}), group=Group(False))
    assert sweep.GROUP_TARGET in disposition.unknown and sweep.cleanup_unknown(record, disposition)
    assert "a" in names(disposition.released)  # the release that did happen is still reported


def test_rows_and_read_back(tmp_path: Any) -> None:
    """One `sweep_disposition` row per target other than the group, durable in the ledger, and the
    disposition read back from the rows equals the sweep's."""
    engine = Engine(present={"a"}, unobservable={"u"})
    record = folded(
        ticket("a"),
        ticket("u"),
        ticket("d", lf.Durable(lf.DurableOwner.HOST)),
        ticket("n", confirmation=NOT_APPLIED),
    )
    result = sweep.sweep_detailed(record, Group(), BUDGET, LIMITS, io=engine.io())
    ledger = RunLedger.open(tmp_path / "ledger.ndjson")
    ledger.append("created", run_id="r_x")
    sweep.write_rows(ledger, "r_x", result)
    rows = [r for r in ledger.records if r["kind"] == "sweep_disposition"]
    assert len(rows) == 4  # not the group target
    assert {r["target"]["effect"]: r["disposition"] for r in rows} == {
        "a": "released",
        "u": "unknown",
        "d": "left_durable",
        "n": "nothing_created",
    }
    assert {r["form"] for r in rows} == {"argv_release", "durable"}
    again = sweep.disposition_from_ledger(RunLedger.open(ledger.path).records, record, Group())
    assert again == result.disposition


@pytest.mark.parametrize("sa", ["SA-05"])
def test_operator_limits_defaults_and_plain_plugin_writes_no_rows(sa: str) -> None:
    limits = TrestleConfig.defaults().operator_limits
    assert limits.release_executables == frozenset()
    result = sweep.sweep_detailed(folded(), Group(), BUDGET, limits)
    assert result.rows == () and result.disposition.released == (sweep.GROUP_TARGET,)
