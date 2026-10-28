"""The child's AttemptLane (L.SV-1.2; B2-C7, MC-19): the durable claim/attempt record.

One serialized appender per process (a single `threading.Lock` for every entry of every lane),
every entry made durable (fsync of the file and its directory, through
`trestle.common.fsutil.append_ndjson`, CS-1 framing) before the call that wrote it returns, and
append-only past the committed length: a repair drops only an uncommitted framed tail, never a
committed byte. The lane states no rule of its own: what it writes, and what it refuses, are
B2-C7's and V-4's, cited by name in each method.

Capacity (B2-C7). The lane's entry count is fixed per root at construction (`lane_entries`, the
admitted `PlanAccepted.lane_entries`). `record_plan` reserves one `record_end` slot per vertex of
the walked set `V_run` (V-4.8); `issue` returns a ticket only if it can also reserve that
ticket's confirmation and its result-or-release slot (three slots per ticket). So `confirm`,
`record_result`, `record_released` and `record_end` never meet a full lane; only `issue` and
`record_step` can. The plan entry's own slot is reserved from construction, so nothing but the
plan can take it.

State is one fold over the committed entries (`_apply`), used for every write and to re-read the
file after a failed append, so memory never disagrees with the file about what is durable.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, final

from trestle.common import lane_format as lf
from trestle.common.fsutil import append_ndjson

_APPEND_LOCK = threading.Lock()  # the one serialized appender of the process (B2-C7)

type Key = tuple[tuple[str, ...], str, int]  # (path, effect, attempt): V-4.3 without the root


class TicketRefusal(StrEnum):
    """V-4 `TicketRefusal`: the values the lane owns. `UNDECLARED_EFFECT`, `WRONG_FACET_CLASS`
    and `RELEASE_POINT_PASSED` are `Ticketed`'s (B1-C6), never the lane's, and are not here."""

    ONCE_ALREADY_ISSUED = "once_already_issued"
    IN_DOUBT = "in_doubt"
    ATTEMPTS_SPENT = "attempts_spent"
    LANE_UNAVAILABLE = "lane_unavailable"
    PLAN_NOT_RECORDED = "plan_not_recorded"


@final
@dataclass(frozen=True, slots=True)
class AttemptTicket:
    """V-4 `AttemptTicket`. Issued only by the attempt lane, after its issue entry is durable."""

    lineage: lf.Lineage
    effect: str
    facet: lf.EffectFacetClass
    attempt: int
    repeat: lf.Repeat
    lifetime: lf.Lifetime
    release: lf.ReleaseDescriptor
    remedy: lf.RemedyGrant | None


def _key(path: tuple[str, ...], effect: str, attempt: int) -> Key:
    return (path, effect, attempt)


class AttemptLane:
    def __init__(
        self,
        run_dir: Path,
        lane_entries: int,
        scope: Iterable[Any],
        *,
        now: Callable[[], datetime] | None = None,
        append: Callable[[Path, dict[str, Any]], None] | None = None,
    ) -> None:
        # a node path is a tuple of names, or any object with `.segments` (B2's NodePath)
        self._scope = frozenset(tuple(getattr(p, "segments", p)) for p in scope)
        if lane_entries < len(self._scope) + 1:
            raise ValueError(
                f"lane_entries {lane_entries} < |scope| + 1 = {len(self._scope) + 1}: the plan "
                "entry and every NodeEnd slot record_plan may reserve must fit"
            )
        self._capacity = lane_entries
        self._path = lf.lane_path(run_dir)
        self._now = now or (lambda: datetime.now(UTC))
        self._append = append or append_ndjson
        with _APPEND_LOCK:
            self._reset()
            if self._path.exists():
                for entry in lf.read_lane(self._path).entries:
                    self._apply(entry)
            self._committed = lf.committed_length(self._path)

    # ------------------------------------------------------------------ state (one fold)

    def _reset(self) -> None:
        self._entries: list[lf.Entry] = []
        self._seq = 0
        self._plan: lf.PlanIdentity | None = None
        self._end_open: set[tuple[str, ...]] = set()
        self._ended: set[tuple[str, ...]] = set()
        self._issue: dict[Key, lf.IssueEntry] = {}
        self._status: dict[Key, lf.ConfirmationStatus | None] = {}
        self._followups: dict[Key, int] = {}  # reserved follow-up slots not yet used
        self._by_effect: dict[tuple[tuple[str, ...], str], list[Key]] = {}
        self._latest: dict[tuple[str, ...], Key] = {}
        self._handles: dict[tuple[tuple[str, ...], str, str], Key] = {}
        self._ticket_reserved = 0

    def _apply(self, entry: lf.Entry) -> None:
        """Fold one committed entry into the state."""
        self._entries.append(entry)
        self._seq = max(self._seq, entry.seq)
        match entry:
            case lf.PlanEntry(plan=plan):
                if self._plan is None:
                    self._plan = plan
                    self._end_open = set(lf.walked_set(self._scope, plan.selection)) - self._ended
            case lf.IssueEntry():
                key = _key(entry.lineage.path, entry.effect, entry.attempt)
                if key not in self._issue:
                    self._issue[key] = entry
                    self._status[key] = None
                    self._followups[key] = 2
                    self._ticket_reserved += 2
                    self._by_effect.setdefault((key[0], key[1]), []).append(key)
                    self._latest[key[0]] = key
            case lf.ConfirmationEntry():
                key = _key(entry.lineage.path, entry.effect, entry.attempt)
                if key in self._issue and self._status[key] is None:
                    self._status[key] = entry.confirmation.status
                    self._use_followup(key)
                    issue = self._issue[key]
                    conf = entry.confirmation
                    if (
                        conf.status == lf.ConfirmationStatus.APPLIED
                        and issue.facet == lf.EffectFacetClass.CREATE
                        and conf.identity is not None
                    ):
                        self._handles[(key[0], key[1], conf.identity)] = key
            case lf.ResultEntry() | lf.ReleasedEntry():
                key = _key(entry.lineage.path, entry.effect, entry.attempt)
                if key in self._issue:
                    self._use_followup(key)
            case lf.NodeEnd():
                self._ended.add(entry.lineage.path)
                self._end_open.discard(entry.lineage.path)
            case lf.StepEntry():
                pass

    def _use_followup(self, key: Key) -> None:
        if self._followups.get(key, 0) > 0:
            self._followups[key] -= 1
            self._ticket_reserved -= 1

    def _reserved(self) -> int:
        return (0 if self._plan is not None else 1) + len(self._end_open) + self._ticket_reserved

    def _free(self) -> int:
        """Slots no reservation holds."""
        return self._capacity - len(self._entries) - self._reserved()

    def _resync(self) -> None:
        """Re-read the file after an append that raised, so memory is what the file holds (a
        rollback that itself failed leaves the line in the file, and then it counts: fail
        closed, at worst an unconfirmed ticket that no effect followed)."""
        self._reset()
        try:
            for entry in lf.read_lane(self._path).entries:
                self._apply(entry)
        except OSError:
            pass

    def _write(self, entry: lf.Entry) -> None:
        """Encode (bounds enforced, `OverBound`), append durably (`OSError`), then fold. Called
        with the lock held."""
        record = lf.encode_record(entry, self._seq + 1)
        try:
            self._append(self._path, record)
        except BaseException:
            self._rollback()
            self._resync()
            raise
        self._committed = self._path.stat().st_size
        self._apply(lf.decode_record(record, self._path.parent.parent.name))

    def _rollback(self) -> None:
        """An append that raised is not durable, so it is not in the lane: cut the file back to
        the committed length this lane last knew (only the bytes of the failed write, never a
        committed byte)."""
        try:
            if self._path.exists() and self._path.stat().st_size > self._committed:
                os.truncate(self._path, self._committed)
        except OSError:
            pass

    # ------------------------------------------------------------------ the surface (B2-C7)

    def record_plan(self, plan: lf.PlanIdentity) -> None:
        """Exactly once, before the first `issue` and the first `record_end`; reserves one
        `record_end` slot per vertex of `V_run` (V-4.8). A second call, an unwritable entry, or
        a plan entry over its bound raises: it is a loop or admission defect, not a refusal."""
        with _APPEND_LOCK:
            if self._plan is not None:
                raise RuntimeError("record_plan is written exactly once")
            self._write(lf.PlanEntry(plan))

    def issue(
        self,
        lineage: lf.Lineage,
        effect: str,
        facet: lf.EffectFacetClass,
        repeat: lf.Repeat,
        lifetime: lf.Lifetime,
        release: lf.ReleaseDescriptor,
        max_attempts: int,
        remedy: lf.RemedyGrant | None,
    ) -> AttemptTicket | TicketRefusal:
        """A ticket only after the issue entry is durable. Refusals, in B2-C7's order:
        `PLAN_NOT_RECORDED`; `ONCE_ALREADY_ISSUED`; `IN_DOUBT` (the node's latest ticket is
        unresolved and this is not a new attempt of that ticket's own SAFE effect, which
        supersedes it, V-4.7); `ATTEMPTS_SPENT`; and `LANE_UNAVAILABLE` for any `LaneRefusal`
        (not durable, no three unreserved slots, a field over its V-13 bound): fail closed, no
        ticket, no effect."""
        path = lineage.path
        with _APPEND_LOCK:
            if self._plan is None:
                return TicketRefusal.PLAN_NOT_RECORDED
            earlier = self._by_effect.get((path, effect), [])
            if repeat == lf.Repeat.ONCE and any(
                self._status[k] != lf.ConfirmationStatus.NOT_APPLIED for k in earlier
            ):
                return TicketRefusal.ONCE_ALREADY_ISSUED
            latest = self._latest.get(path)
            if latest is not None and self._status[latest] in (
                None,
                lf.ConfirmationStatus.UNKNOWN,
            ):
                own_safe = latest[1] == effect and self._issue[latest].repeat == lf.Repeat.SAFE
                if not own_safe:
                    return TicketRefusal.IN_DOUBT
            attempt = len(earlier) + 1
            if attempt > max_attempts:
                return TicketRefusal.ATTEMPTS_SPENT
            if self._free() < 3:
                return TicketRefusal.LANE_UNAVAILABLE
            entry = lf.IssueEntry(
                lineage=lineage,
                effect=effect,
                facet=facet,
                attempt=attempt,
                repeat=repeat,
                lifetime=lifetime,
                release=release,
                remedy=remedy,
                issued_at=self._now(),
            )
            try:
                self._write(entry)
            except (OSError, lf.OverBound):
                return TicketRefusal.LANE_UNAVAILABLE
        return AttemptTicket(lineage, effect, facet, attempt, repeat, lifetime, release, remedy)

    def confirm(
        self, ticket: AttemptTicket, confirmation: lf.Confirmation
    ) -> lf.CreatedHandle | None:
        """Into the ticket's reserved slot. A `CreatedHandle` (carrying the ticket's descriptor)
        iff the recorded facet is CREATE and the status APPLIED; `identity` is then required
        (V-3), so its absence raises before anything is written."""
        creates = (
            ticket.facet == lf.EffectFacetClass.CREATE
            and confirmation.status == lf.ConfirmationStatus.APPLIED
        )
        if creates and confirmation.identity is None:
            raise ValueError("an APPLIED creation must carry the run-scoped selector (identity)")
        with _APPEND_LOCK:
            key = self._known(ticket.lineage.path, ticket.effect, ticket.attempt)
            if self._status[key] is not None:
                raise ValueError("ticket already confirmed")
            self._write(
                lf.ConfirmationEntry(ticket.lineage, ticket.effect, ticket.attempt, confirmation)
            )
        if not creates:
            return None
        assert confirmation.identity is not None
        return lf.CreatedHandle(
            ticket.lineage, ticket.effect, confirmation.identity, ticket.release
        )

    def record_result(self, ticket: AttemptTicket, result: lf.RecordedResult) -> None:
        """Into the ticket's reserved slot (V-5.5)."""
        with _APPEND_LOCK:
            self._known(ticket.lineage.path, ticket.effect, ticket.attempt)
            self._write(lf.ResultEntry(ticket.lineage, ticket.effect, ticket.attempt, result))

    def record_step(self, step: lf.StepEntry) -> None | lf.LaneRefusal:
        """None once the entry is durable. `FULL` (no unreserved slot) and `UNAVAILABLE` write
        nothing and the loop keeps the step in process (V-4.5); `OVER_BOUND` writes nothing (a
        loop defect, B1-C7)."""
        with _APPEND_LOCK:
            return self._write_unreserved(step)

    def record_released(self, handle: lf.CreatedHandle, outcome: str | None) -> None:
        """Into the ticket's reserved slot, only after observed absence (V-3.8): the caller's
        obligation. `handle` must be one this lane confirmed."""
        with _APPEND_LOCK:
            key = self._handles.get((handle.lineage.path, handle.effect, handle.selector))
            if key is None:
                raise ValueError("not a handle this lane confirmed")
            self._write(
                lf.ReleasedEntry(handle.lineage, handle.effect, key[2], self._now(), outcome)
            )

    def record_end(self, end: lf.NodeEnd) -> None | lf.LaneRefusal:
        """Into the vertex's reserved slot (V-4.8): never `FULL` for a vertex of `V_run`.
        `DUPLICATE` (a second end for a vertex: nothing written, the first stands),
        `UNAVAILABLE` (the vertex stays unended, its slot still reserved), `OVER_BOUND`."""
        path = end.lineage.path
        with _APPEND_LOCK:
            if path in self._ended:
                return lf.LaneRefusal.DUPLICATE
            if self._plan is None:
                raise RuntimeError("record_end before record_plan")
            if path in self._end_open:
                return self._write_reserved(end)
            return self._write_unreserved(end)

    def node_record(self, path: tuple[str, ...]) -> lf.NodeRecord:
        """V-4.5's durable part for `path`."""
        with _APPEND_LOCK:
            entries = tuple(self._entries)
        return lf.node_record(entries, tuple(path))

    def committed_length(self) -> int:
        """The bytes of the committed prefix of the lane file (monotone: a repair only drops
        what lies past it)."""
        return lf.committed_length(self._path)

    # ------------------------------------------------------------------ helpers

    def _known(self, path: tuple[str, ...], effect: str, attempt: int) -> Key:
        key = _key(path, effect, attempt)
        if key not in self._issue:
            raise ValueError("not a ticket this lane issued")
        return key

    def _write_reserved(self, entry: lf.Entry) -> lf.LaneRefusal | None:
        try:
            self._write(entry)
        except lf.OverBound:
            return lf.LaneRefusal.OVER_BOUND
        except OSError:
            return lf.LaneRefusal.UNAVAILABLE
        return None

    def _write_unreserved(self, entry: lf.Entry) -> lf.LaneRefusal | None:
        if self._free() < 1:
            return lf.LaneRefusal.FULL
        return self._write_reserved(entry)
