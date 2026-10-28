"""A stub release engine and record builders for the sweep and recovery tests (L.SV-3.7, 3.8):
the engine keeps instances by name and logs every command the sweep runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.proof import tolerances
from trestle.common import lane_format as lf
from trestle.common.plan.compiler import AdmittedPlan
from trestle.server import fold, sweep
from trestle.server.config import OperatorLimits

EXE = "/opt/engine/ctl"
STEP = timedelta(seconds=tolerances.SETTLE_LONG_S)  # a release command's timeout
T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
CONFIRMED = lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, "sel")
NOT_APPLIED = lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "stop_seen", None)
UNSURE = lf.Confirmation(lf.ConfirmationStatus.UNKNOWN, None, None)


def descriptor(name: str, *, remove: bool = True, exe: str = EXE) -> lf.ArgvRelease:
    return lf.ArgvRelease(
        executable=exe,
        observe_argv=("obs", name),
        observe_ok_exit=frozenset({0}),
        stop_argv=("stop", name),
        timeout=STEP,
        remove_argv=("rm", name) if remove else None,
    )


def ticket(
    name: str,
    release: lf.ReleaseDescriptor | None = None,
    *,
    path: tuple[str, ...] = (),
    confirmation: lf.Confirmation | None = CONFIRMED,
    issued_at: datetime = T0,
    released_at: datetime | None = None,
) -> lf.TicketEntry:
    return lf.TicketEntry(
        lineage=lf.Lineage("run-root-0001", path),
        effect=name,
        facet=lf.EffectFacetClass.CREATE,
        attempt=1,
        repeat=lf.Repeat.SAFE,
        lifetime=lf.Lifetime.RUN,
        release=release if release is not None else descriptor(name),
        remedy=None,
        issued_at=issued_at,
        confirmation=confirmation,
        released_at=released_at,
    )


def folded(
    *entries: lf.TicketEntry,
    unknown_paths: tuple[str, ...] = (),
    overflowed: bool = False,
    refused_full: bool = False,
) -> fold.FoldedRecord:
    return fold.FoldedRecord(
        root="run-root-0001",
        stop_rows=(),
        plan=None,
        entries=tuple(entries),
        steps=(),
        ends=(),
        ended=False,
        unconfirmed=tuple(e for e in entries if e.confirmation is None),
        unknown_paths=unknown_paths,
        overflowed=overflowed,
        refused_full=refused_full,
    )


@dataclass
class Group:
    confirmed_gone: bool = True


@dataclass
class Engine:
    """A stub release engine: instances by name, a call log, and the failures a test plants."""

    present: set[str] = field(default_factory=set)
    stop_fails: set[str] = field(default_factory=set)
    remove_fails: set[str] = field(default_factory=set)
    stop_leaves_it: set[str] = field(default_factory=set)  # stopped, but still observable
    unobservable: set[str] = field(default_factory=set)
    timing_out: set[str] = field(default_factory=set)
    huge_output: set[str] = field(default_factory=set)
    calls: list[tuple[str, str]] = field(default_factory=list)
    active: int = 0
    peak: int = 0

    def run(self, release: lf.ArgvRelease, argv: Any, timeout_s: float) -> sweep.CommandResult:
        assert release.executable == EXE and timeout_s > 0
        role, name = argv[0], argv[1]
        self.calls.append((role, name))
        if name in self.timing_out:
            return sweep.CommandResult(exit=None, timed_out=True)
        if role == "obs":
            if name in self.unobservable:
                return sweep.CommandResult(exit=2)
            if name in self.huge_output:
                return sweep.CommandResult(exit=0, stdout=b"x" * 5000)
            return sweep.CommandResult(exit=0, stdout=b"id\n" if name in self.present else b"")
        if role == "stop":
            if name in self.stop_fails:
                return sweep.CommandResult(exit=1)
            if name not in self.stop_leaves_it:
                self.present.discard(name)
            return sweep.CommandResult(exit=0)
        assert role == "rm"
        if name in self.remove_fails:
            return sweep.CommandResult(exit=1)
        self.present.discard(name)
        return sweep.CommandResult(exit=0)

    def io(self, **overrides: Any) -> sweep.SweepIO:
        return sweep.SweepIO(run=self.run, **overrides)

    def roles(self, name: str) -> list[str]:
        return [role for role, n in self.calls if n == name]


LIMITS = OperatorLimits(release_executables=frozenset({EXE}), sweep_parallelism=1)
BUDGET = timedelta(seconds=tolerances.JOIN_WAIT_S)


def run_sweep(
    record: fold.FoldedRecord,
    engine: Engine,
    *,
    group: Group | None = None,
    limits: OperatorLimits = LIMITS,
    budget: timedelta = BUDGET,
    plan: AdmittedPlan | None = None,
    recovery: bool = False,
    io: sweep.SweepIO | None = None,
) -> sweep.CleanupDisposition:
    return sweep.sweep(
        record,
        group or Group(),
        budget,
        limits,
        plan,
        recovery=recovery,
        io=io or engine.io(),
    )


def names(targets: Any) -> list[str | None]:
    return [t.effect for t in targets]
