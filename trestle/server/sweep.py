"""The plan-rank release sweep (L.SV-3.7; B2-C9 `HardStopRelease.sweep`, V-10).

`sweep(folded, group, budget, limits, plan)` releases what a run left behind from its record alone,
with no plugin code: the targets are named by record data (the fold's `TicketEntry`s), grouped by
equal descriptor (V-10.1), and each is disposed by its descriptor form only (V-10.3):

* the run's process group is always one target, `SweepTarget(None, None)`, disposed from the
  `GroupStop` alone (released only when the host confirmed the group gone and no unrecorded
  `InRunGroup` entry disclosed helpers; otherwise unknown; never `nothing_created`);
* `Durable` is `left_durable` with its owner;
* `ArgvRelease` runs only when its executable is on the operator's allowlist
  (`OperatorLimits.release_executables`, empty by default): observe, stop, remove, observe
  (V-10.4); released means observed absent;
* a target whose every entry is `NOT_APPLIED` is `nothing_created` and nothing is run.

Targets go in descending release rank (a plan-less root: every target at rank 0), in reverse issue
order inside a node, at most `sweep_parallelism` at once and only within a rank. Every target the
budget does not reach is `unknown`. An entry outside the admitted plan is never a target
(`folded.unknown_paths`); it, an overflowed lane, or any `unknown` target makes the cleanup
`unknown` (`cleanup_unknown`), never clean. A `refused_full` lane alone changes nothing.

Reads the lane only through `fold` (the host's one reader of the lane codec). `sweep_detailed` also
returns the per-target rows that `write_rows` appends to the ledger, one `sweep_disposition` row
per target other than the group target, durable before the terminal row (B4-C7): the group
target's disposition is `group_stop`'s, so a plain plugin's ledger is unchanged.
"""

from __future__ import annotations

import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from trestle.common.plan import bounds
from trestle.common.plan.compiler import AdmittedPlan
from trestle.server import fold
from trestle.server.config import OperatorLimits
from trestle.server.ledger import RunLedger

SWEEP_DISPOSITION_KIND = "sweep_disposition"
# Written instead of a sweep when recovery must not sweep (a plan of an unknown format): the run's
# cleanup is unknown, and nothing was released.
SWEEP_SKIPPED_KIND = "sweep_skipped"
SKIPPED_UNKNOWN_PLAN_FORMAT = "unknown_plan_format"

FORM_GROUP = "in_run_group"
FORM_ARGV = "argv_release"
FORM_DURABLE = "durable"

RELEASED = "released"
NOTHING_CREATED = "nothing_created"
UNKNOWN = "unknown"
LEFT_DURABLE = "left_durable"


@dataclass(frozen=True, slots=True)
class SweepTarget:
    """B2 `SweepTarget`: the lineage path and effect of the target's earliest issue entry; None
    for both is the run's process-group target."""

    path: tuple[str, ...] | None
    effect: str | None


GROUP_TARGET = SweepTarget(None, None)


@dataclass(frozen=True, slots=True)
class CleanupDisposition:
    """B2 `CleanupDisposition`. `left_durable` pairs a target with its owner (`host` /
    `environment`)."""

    released: tuple[SweepTarget, ...] = ()
    nothing_created: tuple[SweepTarget, ...] = ()
    unknown: tuple[SweepTarget, ...] = ()
    left_durable: tuple[tuple[SweepTarget, str], ...] = ()

    def all_targets(self) -> tuple[SweepTarget, ...]:
        return (
            *self.released,
            *self.nothing_created,
            *self.unknown,
            *(t for t, _ in self.left_durable),
        )


@dataclass(frozen=True, slots=True)
class TargetRow:
    """One `sweep_disposition` ledger row's content."""

    target: SweepTarget
    form: str
    disposition: str
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class SweepResult:
    disposition: CleanupDisposition
    rows: tuple[TargetRow, ...]


class GroupLike(Protocol):
    @property
    def confirmed_gone(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a release command did: its exit status (None on a timeout) and stdout (bounded)."""

    exit: int | None
    stdout: bytes = b""
    timed_out: bool = False


def run_command(release: fold.ArgvRelease, argv: Sequence[str], timeout_s: float) -> CommandResult:
    """Run one release command as V-10 says: no shell, stdin closed, an environment built from
    empty, bounded by `timeout_s`; `release.executable` is what is executed."""
    try:
        done = subprocess.run(
            list(argv),
            executable=release.executable,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            timeout=max(timeout_s, 0.001),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(exit=None, timed_out=True)
    except OSError:
        return CommandResult(exit=None)
    return CommandResult(exit=done.returncode, stdout=done.stdout[: bounds.OBSERVE_STDOUT_MAX + 1])


@dataclass(frozen=True, slots=True)
class SweepIO:
    """The seams of the sweep: how a command runs and what time it is. Tests inject their own."""

    run: Callable[[fold.ArgvRelease, Sequence[str], float], CommandResult] = run_command
    now: Callable[[], datetime] = field(default=lambda: datetime.now(tz=UTC))
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


@dataclass(slots=True)
class _Target:
    sweep_target: SweepTarget
    descriptor: fold.ArgvRelease | fold.Durable
    entries: list[fold.TicketEntry]
    order: int  # position of the earliest issue entry in the record
    rank: int

    @property
    def form(self) -> str:
        return FORM_ARGV if isinstance(self.descriptor, fold.ArgvRelease) else FORM_DURABLE


def _status(entry: fold.TicketEntry) -> fold.ConfirmationStatus | None:
    return entry.confirmation.status if entry.confirmation is not None else None


def _rank_of(plan: AdmittedPlan | None, path: tuple[str, ...]) -> int:
    if plan is None:
        return 0
    return int(plan.release_rank.get("/".join(path), 0))


def group_disposition(entries: Iterable[fold.TicketEntry], group: GroupLike) -> str:
    """The process-group target's disposition, from `GroupStop` alone (B2-C9): released only when
    the host confirmed the group gone and no unrecorded `InRunGroup` entry of an effect that took
    place disclosed helpers (`helpers_disclosed`); otherwise unknown, never `nothing_created`."""
    if not group.confirmed_gone:
        return UNKNOWN
    for entry in entries:
        release = entry.release
        if (
            isinstance(release, fold.InRunGroup)
            and release.helpers_disclosed
            and entry.released_at is None
            and _status(entry) != fold.ConfirmationStatus.NOT_APPLIED
        ):
            return UNKNOWN
    return RELEASED


def cleanup_unknown(folded: fold.FoldedRecord, disposition: CleanupDisposition) -> bool:
    """B2-C9 Post: an overflowed lane, an entry outside the admitted plan or any `unknown` target
    makes the cleanup unknown, never clean. `refused_full` alone does not."""
    return bool(disposition.unknown) or fold.cleanup_is_unknown(folded)


def _collect(folded: fold.FoldedRecord, plan: AdmittedPlan | None) -> list[_Target]:
    """The targets other than the group: every entry not recorded released, grouped by equal
    descriptor (V-10.1); `InRunGroup` entries are the group target's."""
    grouped: dict[Any, _Target] = {}
    for position, entry in enumerate(folded.entries):
        if entry.released_at is not None:
            continue
        release = entry.release
        if isinstance(release, fold.InRunGroup):
            continue
        target = grouped.get(release)
        if target is None:
            grouped[release] = _Target(
                SweepTarget(entry.lineage.path, entry.effect),
                release,
                [entry],
                position,
                _rank_of(plan, entry.lineage.path),
            )
        else:
            target.entries.append(entry)
    return list(grouped.values())


class _Budget:
    def __init__(self, io: SweepIO, budget: timedelta) -> None:
        self._io = io
        self._end = io.monotonic() + budget.total_seconds()

    def remaining(self) -> float:
        return self._end - self._io.monotonic()


class _Resolver:
    """Disposes one target. Stateless between targets but for the shared budget."""

    def __init__(
        self,
        io: SweepIO,
        budget: _Budget,
        limits: OperatorLimits,
        group: GroupLike,
        recovery: bool,
    ) -> None:
        self.io = io
        self.budget = budget
        self.limits = limits
        self.group = group
        self.recovery = recovery

    def resolve(self, target: _Target) -> tuple[str, str | None]:
        statuses = [_status(e) for e in target.entries]
        if all(s == fold.ConfirmationStatus.NOT_APPLIED for s in statuses):
            return NOTHING_CREATED, None  # nothing took place; nothing is run
        descriptor = target.descriptor
        if isinstance(descriptor, fold.Durable):
            return LEFT_DURABLE, descriptor.owner.value
        if self.budget.remaining() <= 0:
            return UNKNOWN, None  # the budget did not reach it
        return self._argv(target, descriptor, statuses), None

    # -- ArgvRelease (V-10.4) ---------------------------------------------------------------

    def _command(self, release: fold.ArgvRelease, argv: Sequence[str]) -> CommandResult | None:
        remaining = self.budget.remaining()
        if remaining <= 0:
            return None
        return self.io.run(release, argv, min(release.timeout.total_seconds(), remaining))

    def _observe(self, release: fold.ArgvRelease) -> str:
        """`present`, `absent` or `unknown` (could not observe), by V-10.4's rule."""
        result = self._command(release, release.observe_argv)
        if result is None or result.timed_out or result.exit not in release.observe_ok_exit:
            return UNKNOWN
        if len(result.stdout) > bounds.OBSERVE_STDOUT_MAX:
            return UNKNOWN
        return "absent" if not result.stdout.strip() else "present"

    def _stop_and_remove(self, release: fold.ArgvRelease) -> str:
        """The present-instance path: stop once, remove once (only after a stop that exited 0),
        observe again; released only when observed absent."""
        stop = self._command(release, release.stop_argv)
        if stop is None or stop.timed_out or stop.exit != 0:
            return UNKNOWN  # a failed stop skips the remove, and is not retried
        if release.remove_argv is not None:
            remove = self._command(release, release.remove_argv)
            if remove is None or remove.timed_out or remove.exit != 0:
                return UNKNOWN
        return RELEASED if self._observe(release) == "absent" else UNKNOWN

    def _argv(
        self,
        target: _Target,
        release: fold.ArgvRelease,
        statuses: list[fold.ConfirmationStatus | None],
    ) -> str:
        if not release.executable or release.executable not in self.limits.release_executables:
            return UNKNOWN  # never run what the operator did not list
        confirmed = any(s == fold.ConfirmationStatus.APPLIED for s in statuses)
        first = self._observe(release)
        if first == UNKNOWN:
            return UNKNOWN
        if first == "present":
            return self._stop_and_remove(release)
        # observed absent on the first look
        if confirmed:
            return RELEASED
        if self.recovery:
            return UNKNOWN  # never nothing_created in recovery (a re-sweep must not downgrade)
        return self._settled_absent(target, release)

    def _settled_absent(self, target: _Target, release: fold.ArgvRelease) -> str:
        """An unconfirmed target seen absent is `nothing_created` only if the look came after the
        group was confirmed gone and after `timeout` has passed since the latest issue entry;
        otherwise wait until then (within the budget), look once more, and if still unable,
        unknown."""
        if not self.group.confirmed_gone:
            return UNKNOWN
        latest = max(e.issued_at for e in target.entries)
        settle_at = latest + release.timeout
        wait = (settle_at - self.io.now()).total_seconds()
        if wait <= 0:
            return NOTHING_CREATED
        if wait >= self.budget.remaining():
            return UNKNOWN
        self.io.sleep(wait)
        again = self._observe(release)
        if again == "absent":
            return NOTHING_CREATED
        if again == "present":
            return self._stop_and_remove(release)
        return UNKNOWN


def sweep_detailed(
    folded: fold.FoldedRecord,
    group: GroupLike,
    budget: timedelta,
    limits: OperatorLimits,
    plan: AdmittedPlan | None = None,
    *,
    recovery: bool = False,
    io: SweepIO | None = None,
) -> SweepResult:
    """`sweep`, with the per-target rows for the ledger. `recovery` is B2-C11's: an unconfirmed
    target is never `nothing_created` there. `plan` is the admitted plan (None: the implicit
    depth-1 plan, every target at rank 0)."""
    io = io or SweepIO()
    clock_budget = _Budget(io, budget)
    resolver = _Resolver(io, clock_budget, limits, group, recovery)

    by_rank: dict[int, list[_Target]] = defaultdict(list)
    for target in _collect(folded, plan):
        by_rank[target.rank].append(target)

    released: list[SweepTarget] = []
    nothing_created: list[SweepTarget] = []
    unknown: list[SweepTarget] = []
    left_durable: list[tuple[SweepTarget, str]] = []
    rows: list[TargetRow] = []

    workers = max(1, limits.sweep_parallelism)
    for rank in sorted(by_rank, reverse=True):  # descending release rank (V-4.4)
        targets = sorted(by_rank[rank], key=lambda t: t.order, reverse=True)  # reverse issue order
        if workers == 1 or len(targets) == 1:
            results = [resolver.resolve(t) for t in targets]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(resolver.resolve, targets))
        for target, (disposition, owner) in zip(targets, results, strict=True):
            rows.append(TargetRow(target.sweep_target, target.form, disposition, owner))
            if disposition == RELEASED:
                released.append(target.sweep_target)
            elif disposition == NOTHING_CREATED:
                nothing_created.append(target.sweep_target)
            elif disposition == LEFT_DURABLE:
                left_durable.append((target.sweep_target, owner or ""))
            else:
                unknown.append(target.sweep_target)

    # the process-group target: always present, disposed from GroupStop alone
    if group_disposition(folded.entries, group) == RELEASED:
        released.append(GROUP_TARGET)
    else:
        unknown.append(GROUP_TARGET)

    return SweepResult(
        CleanupDisposition(
            tuple(released), tuple(nothing_created), tuple(unknown), tuple(left_durable)
        ),
        tuple(rows),
    )


def sweep(
    folded: fold.FoldedRecord,
    group: GroupLike,
    budget: timedelta,
    limits: OperatorLimits,
    plan: AdmittedPlan | None = None,
    *,
    recovery: bool = False,
    io: SweepIO | None = None,
) -> CleanupDisposition:
    """B2-C9's `HardStopRelease.sweep`."""
    return sweep_detailed(folded, group, budget, limits, plan, recovery=recovery, io=io).disposition


def budget_for(limits: OperatorLimits) -> timedelta:
    """The sweep's share of the finalization margin: what is left after the kill (B2-C2 (5) sizes
    the margin as `grace + kill + the sweep`)."""
    return timedelta(seconds=max(limits.finalization_margin - limits.grace - limits.kill, 0.0))


def write_rows(ledger: RunLedger, run_id: str, result: SweepResult) -> None:
    """One `sweep_disposition{target, form, disposition}` row per target other than the group's,
    in the order the sweep took them; durable before the terminal row (B4-C7)."""
    for row in result.rows:
        fields: dict[str, Any] = {
            "run_id": run_id,
            "target": {
                "path": "/".join(row.target.path or ()),
                "effect": row.target.effect,
            },
            "form": row.form,
            "disposition": row.disposition,
        }
        if row.owner is not None:
            fields["owner"] = row.owner
        ledger.append(SWEEP_DISPOSITION_KIND, **fields)


def disposition_from_ledger(
    records: Iterable[Mapping[str, Any]],
    folded: fold.FoldedRecord,
    group: GroupLike,
) -> CleanupDisposition:
    """The run's cleanup disposition rebuilt from its durable rows (the `sweep_disposition` rows
    and the group stop), for the answer's recomputation at read time (B4-C1)."""
    records_list = list(records)
    released: list[SweepTarget] = []
    nothing_created: list[SweepTarget] = []
    unknown: list[SweepTarget] = []
    left_durable: list[tuple[SweepTarget, str]] = []
    for record in records_list:
        if record.get("kind") != SWEEP_DISPOSITION_KIND:
            continue
        raw = record.get("target")
        raw = raw if isinstance(raw, Mapping) else {}
        text = str(raw.get("path", ""))
        target = SweepTarget(tuple(text.split("/")) if text else (), raw.get("effect"))
        disposition = record.get("disposition")
        if disposition == RELEASED:
            released.append(target)
        elif disposition == NOTHING_CREATED:
            nothing_created.append(target)
        elif disposition == LEFT_DURABLE:
            left_durable.append((target, str(record.get("owner", ""))))
        else:
            unknown.append(target)
    skipped = any(r.get("kind") == SWEEP_SKIPPED_KIND for r in records_list)
    if not skipped and group_disposition(folded.entries, group) == RELEASED:
        released.append(GROUP_TARGET)
    else:  # a sweep recovery declined (unknown plan format) leaves the cleanup unknown
        unknown.append(GROUP_TARGET)
    return CleanupDisposition(
        tuple(released), tuple(nothing_created), tuple(unknown), tuple(left_durable)
    )
