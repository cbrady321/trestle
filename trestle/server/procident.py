"""Process identity and attribution (MC-CORE-05; B2-C16, V-2.3, DM-66).

One `ProcessIdentity` ledger row per process the run supervisor attributes to a run, appended
before the supervisor relies on the attribution (signals the process or counts it gone). A
process is *attributable to a run* when a snapshot of the process table, taken after spawn, at
each liveness poll and immediately before a signal, shows it is the run's leader, a member of the
run's recorded process group, or a descendant by parent id of a process already attributable,
whatever session or group it has since moved to.

Identity is `(pid, boot, start)`. `boot` is the host's boot identity (darwin
`kern.bootsessionuuid`, Linux `/proc/sys/kernel/random/boot_id`); `start` is a numeric,
zone-free start time within that boot (Linux `/proc/<pid>/stat` field 22, clock ticks since boot;
darwin the process's start time in microseconds since the epoch, read through `proc_pidinfo`). It
is compared as an integer, never as text: no `ps -o lstart`, nothing that depends on the time
zone or the locale. A live pid whose start differs from the recorded one is a reused pid and is
never attributed to the run, never signalled.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

# --- the process table -------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcRow:
    """One process as the table shows it. A zombie is an exited process still awaiting its
    parent's wait: it holds no resources and counts as gone."""

    pid: int
    ppid: int
    pgid: int
    start: int
    zombie: bool = False


class ProcessSource(Protocol):
    """Where the boot identity and the process table come from. The real source reads the
    operating system; a test plants its own."""

    def boot_id(self) -> str: ...

    def table(self) -> dict[int, ProcRow]: ...

    def row(self, pid: int) -> ProcRow | None: ...


_MAXCOMLEN = 16
_PROC_PIDTBSDINFO = 3
_SZOMB = 5


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * _MAXCOMLEN),
        ("pbi_name", ctypes.c_char * (2 * _MAXCOMLEN)),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


@functools.lru_cache(maxsize=1)
def _libproc() -> Any:
    path = ctypes.util.find_library("proc")
    return ctypes.CDLL(path) if path else None


def _darwin_row(pid: int) -> ProcRow | None:
    lib = _libproc()
    if lib is None:
        return None
    info = _ProcBsdInfo()
    size = lib.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size != ctypes.sizeof(info):
        return None
    return ProcRow(
        pid=pid,
        ppid=int(info.pbi_ppid),
        pgid=int(info.pbi_pgid),
        start=int(info.pbi_start_tvsec) * 1_000_000 + int(info.pbi_start_tvusec),
        zombie=info.pbi_status == _SZOMB,
    )


def _darwin_pids() -> list[int]:
    lib = _libproc()
    if lib is None:
        return []
    count = int(lib.proc_listallpids(None, 0))
    if count <= 0:
        return []
    buf = (ctypes.c_int32 * (count * 2))()
    got = int(lib.proc_listallpids(buf, ctypes.sizeof(buf)))
    return [int(buf[i]) for i in range(max(got, 0)) if buf[i] > 0]


def parse_linux_stat(pid: int, raw: str) -> ProcRow | None:
    """A `/proc/<pid>/stat` line. `comm` (field 2) may hold spaces and parentheses, so split after
    the last `)`: `state` is then field 3, `ppid` 4, `pgrp` 5 and `starttime` 22."""
    close = raw.rfind(")")
    if close == -1:
        return None
    fields = raw[close + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return ProcRow(
            pid=pid,
            ppid=int(fields[1]),
            pgid=int(fields[2]),
            start=int(fields[19]),
            zombie=fields[0] in ("Z", "X"),
        )
    except ValueError:
        return None


def _linux_row(pid: int) -> ProcRow | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            return parse_linux_stat(pid, fh.read())
    except OSError:
        return None


def _linux_pids() -> list[int]:
    try:
        return [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return []


class SystemSource:
    """The real process table and boot identity (darwin and Linux)."""

    def boot_id(self) -> str:
        return boot_id()

    def row(self, pid: int) -> ProcRow | None:
        if sys.platform == "darwin":
            return _darwin_row(pid)
        if sys.platform.startswith("linux"):
            return _linux_row(pid)
        return None

    def table(self) -> dict[int, ProcRow]:
        pids = _darwin_pids() if sys.platform == "darwin" else _linux_pids()
        rows: dict[int, ProcRow] = {}
        for pid in pids:
            row = self.row(pid)
            if row is not None:
                rows[pid] = row
        return rows


SYSTEM: ProcessSource = SystemSource()


@functools.lru_cache(maxsize=1)
def boot_id() -> str:
    """The host's boot identity: constant for the life of the boot, unlike a wall-clock reading."""
    if sys.platform == "darwin":
        proc = subprocess.run(
            ["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True, text=True, check=False
        )
        return proc.stdout.strip()
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def start_time(pid: int) -> int | None:
    """A numeric, zone-free start time for `pid` within this boot, or None when it cannot be read
    (the process is gone)."""
    row = SYSTEM.row(pid)
    return None if row is None else row.start


# --- identity rows -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """B2-C16's `ProcessIdentity`. `start` is held as an integer and written as its decimal text
    (the interface hides the format; it is numeric and compared as an integer)."""

    pid: int
    boot: str
    start: int
    group: int
    leader: bool

    def fields(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "boot": self.boot,
            "start": str(self.start),
            "group": self.group,
            "leader": self.leader,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Identity | None:
        """The identity a ledger row records, or None for a row that is not a well-formed
        `process_identity` (a reader never trusts a malformed one)."""
        try:
            pid = row["pid"]
            group = row["group"]
            leader = row["leader"]
            boot = row["boot"]
            start = int(row["start"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (isinstance(pid, int) and isinstance(group, int) and isinstance(leader, bool)):
            return None
        if isinstance(pid, bool) or isinstance(group, bool) or not isinstance(boot, str):
            return None
        return cls(pid=pid, boot=boot, start=start, group=group, leader=leader)


def identity_rows(records: list[dict[str, Any]]) -> list[Identity]:
    """Every well-formed `process_identity` row of a run's ledger, in order."""
    found: list[Identity] = []
    for record in records:
        if record.get("kind") == "process_identity":
            ident = Identity.from_row(record)
            if ident is not None:
                found.append(ident)
    return found


# --- attribution -------------------------------------------------------------------------------


class Attribution:
    """What the supervisor has attributed to one run, and the durable record of it.

    `record` appends the identity's ledger row; it is called for a process *before* `observe`
    returns it, so no caller can signal or count gone a process that has no row. All methods
    take one lock: a run has one attribution however many threads stop it.
    """

    def __init__(
        self,
        *,
        group: int,
        record: Callable[[Identity], None],
        source: ProcessSource | None = None,
        known: list[Identity] | None = None,
    ) -> None:
        self.group = group
        self.source: ProcessSource = source if source is not None else SYSTEM
        self.lock = threading.RLock()
        self.signalled = False
        self.closed = False
        self.last_stop: GroupStop | None = None
        self._record = record
        self._known: dict[tuple[int, int], Identity] = {}
        self._group_live = True
        for ident in known or []:
            self._known[(ident.pid, ident.start)] = ident

    @property
    def identities(self) -> list[Identity]:
        with self.lock:
            return list(self._known.values())

    def attribute_leader(self, pid: int) -> Identity | None:
        """Record the leader's row: after spawn, before the first liveness poll. None when the
        process could not be read (it has already gone); the run then has no identity row and
        recovery takes B2-C11 branch (i)."""
        with self.lock:
            row = self.source.row(pid)
            if row is None or row.zombie:
                return None
            self.group = row.pgid
            ident = Identity(pid, self.source.boot_id(), row.start, row.pgid, True)
            self._register(ident)
            return ident

    def observe(self, table: dict[int, ProcRow] | None = None) -> dict[int, ProcRow]:
        """One ancestry snapshot: every live process attributable to the run now (pid -> row),
        each with its identity row already appended. Called after spawn, at each liveness poll
        and immediately before a signal (V-2.3)."""
        with self.lock:
            live = self._live(table if table is not None else self.source.table())
            attributed = self._attributable(live)
            boot = self.source.boot_id()
            for pid in sorted(attributed):
                row = live[pid]
                if (pid, row.start) not in self._known:
                    self._register(Identity(pid, boot, row.start, row.pgid, False))
            return {pid: live[pid] for pid in attributed}

    def alive(self, table: dict[int, ProcRow] | None = None) -> dict[int, ProcRow]:
        """The recorded processes and their attributable relatives still alive, without writing
        any row (a read for a count, never for a signal)."""
        with self.lock:
            live = self._live(table if table is not None else self.source.table())
            return {pid: live[pid] for pid in self._attributable(live)}

    def group_members(self, alive: dict[int, ProcRow]) -> set[int]:
        """The processes of `alive` that are in the recorded group, while that group has not
        emptied (a signal to the group then reaches only the run's own processes)."""
        with self.lock:
            if not self._group_live:
                return set()
            return {pid for pid, row in alive.items() if row.pgid == self.group}

    def close(self) -> None:
        """The run is over: a later stop returns the last stop's answer and touches nothing."""
        with self.lock:
            self.closed = True

    def _register(self, ident: Identity) -> None:
        self._record(ident)
        self._known[(ident.pid, ident.start)] = ident

    @staticmethod
    def _live(table: dict[int, ProcRow]) -> dict[int, ProcRow]:
        return {pid: row for pid, row in table.items() if not row.zombie}

    def _attributable(self, live: dict[int, ProcRow]) -> set[int]:
        # A recorded process counts only while its pid still carries its recorded start: a live
        # pid with another start is a reused pid, attributable to nobody, and never signalled
        # (B2-C11 (iii), V-2.3), whatever group it is in and whoever its parent is.
        reused = {
            pid
            for (pid, start) in self._known
            if pid in live
            and live[pid].start != start
            and (pid, live[pid].start) not in self._known
        }
        attributed = {
            pid for (pid, start) in self._known if pid in live and live[pid].start == start
        }
        # A recorded group's members count only while the group has not emptied: a pgid can be
        # reused once its group is gone (never while it lives, SA-10).
        if self._group_live:
            members = {
                pid for pid, row in live.items() if row.pgid == self.group and pid not in reused
            }
            if members:
                attributed |= members
            else:
                self._group_live = False
        changed = True
        while changed:
            changed = False
            for pid, row in live.items():
                if pid not in attributed and pid not in reused and row.ppid in attributed:
                    attributed.add(pid)
                    changed = True
        return attributed


# --- the one stopper ---------------------------------------------------------------------------


class Signaller(Protocol):
    """Sends signals. The real one is `os.kill`/`os.killpg`; a test plants its own."""

    def signal_pid(self, pid: int, signum: int) -> None: ...

    def signal_group(self, pgid: int, signum: int) -> None: ...


class OsSignaller:
    def signal_pid(self, pid: int, signum: int) -> None:
        try:
            os.kill(pid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    def signal_group(self, pgid: int, signum: int) -> None:
        try:
            os.killpg(pgid, signum)
        except (ProcessLookupError, PermissionError):
            pass


@dataclass(frozen=True)
class GroupStop:
    """B2's `GroupStop`: `confirmed_gone` is the host's observation that no live process
    attributable to the run remains after the kill (V-2.3). `signalled` says a signal was sent, so
    the recorded `method` is `signal`, else `exit` (nothing was left alive to signal)."""

    confirmed_gone: bool
    signalled: bool

    @property
    def method(self) -> str:
        return "signal" if self.signalled else "exit"


def stop_group(
    attribution: Attribution,
    *,
    signaller: Signaller | None = None,
    grace: float | None = None,
    kill: float | None = None,
    poll: float | None = None,
) -> GroupStop:
    """B2-C10's kill, the one stopper both stop sites call: SIGTERM to every process attributable
    to the run that is still alive (the recorded group, and the descendants by parent id of an
    attributable process, whatever session they moved to), then at most `grace` of waiting, then
    SIGKILL to what is left, then at most `kill` of waiting for confirmation. No sleep before the
    first signal. Every process it signals has its identity row first (`Attribution.observe`
    appends it before returning the process). Whether the leader has exited never exempts its
    descendants. One stop runs at a time per run, and a stop after the run's supervisor closed the
    attribution touches nothing and returns the last answer."""
    from trestle.common import clock

    out = signaller if signaller is not None else OsSignaller()
    grace_s = clock.grace if grace is None else grace
    kill_s = clock.kill if kill is None else kill
    poll_s = clock.poll_interval if poll is None else poll
    with attribution.lock:
        if attribution.closed:
            return attribution.last_stop or GroupStop(False, attribution.signalled)
        alive = attribution.observe()
        if alive:
            _send(attribution, out, alive, signal.SIGTERM)
            attribution.signalled = True
            alive = _wait_gone(attribution, grace_s, poll_s)
        if alive:
            alive = attribution.observe()  # immediately before the signal (V-2.3)
            _send(attribution, out, alive, signal.SIGKILL)
            alive = _wait_gone(attribution, kill_s, poll_s)
        stop = GroupStop(confirmed_gone=not alive, signalled=attribution.signalled)
        attribution.last_stop = stop
        return stop


def _wait_gone(attribution: Attribution, bound: float, poll: float) -> dict[int, ProcRow]:
    """Observe until nothing attributable is alive or `bound` seconds have passed."""
    end = time.monotonic() + bound
    while True:
        alive = attribution.observe()
        remaining = end - time.monotonic()
        if not alive or remaining <= 0:
            return alive
        time.sleep(min(poll, remaining))


def _send(
    attribution: Attribution,
    out: Signaller,
    alive: dict[int, ProcRow],
    signum: int,
) -> None:
    """One signal to the recorded group (while it has members) and to each attributable process
    outside it, after re-reading the process's start so a reused pid is never signalled."""
    in_group = attribution.group_members(alive)
    if in_group:
        out.signal_group(attribution.group, signum)
    for pid, row in sorted(alive.items()):
        if pid in in_group:
            continue
        current = attribution.source.row(pid)
        if current is not None and current.start == row.start and not current.zombie:
            out.signal_pid(pid, signum)


# --- recovery (B2-C11) -------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryDecision:
    """Which B2-C11 branch a restart takes for a run with no terminal row.

    `confirmed_gone` is decided here for branches (i) and (ii), which never signal; branch (iii)
    leaves it None: only the stop that follows can say. `method` is the `group_stop` method
    recorded: `no_identity` for (i), `recovery` for (ii) and (iii)."""

    branch: str
    method: str
    confirmed_gone: bool | None
    identities: tuple[Identity, ...]
    leader: Identity | None


def recovery_decision(
    records: list[dict[str, Any]], source: ProcessSource | None = None
) -> RecoveryDecision:
    """Read the run's `process_identity` rows and take one branch, comparing `(boot, start)` as
    integers and never as text:

    (i) no leader row (a run started by a version that records no identity, K-19, or interrupted
        between spawn and the append): no signal, `confirmed_gone` false, cleanup unknown;
    (ii) the leader's row names a process that is gone (no live process with that pid, boot and
        start): no signal, ever. `confirmed_gone` only when the recorded group is provably gone
        (another boot, or no live process in the recorded group) and every recorded row is gone
        or reused, else false;
    (iii) the leader is alive with a matching start: the caller stops the run's attributable
        processes; only this branch sends a signal.
    """
    src = source if source is not None else SYSTEM
    idents = tuple(identity_rows(records))
    leader = next((i for i in idents if i.leader), None)
    if leader is None:
        return RecoveryDecision("i", "no_identity", False, idents, None)
    boot_now = src.boot_id()
    table = src.table()

    def live_match(ident: Identity) -> bool:
        row = table.get(ident.pid)
        return (
            ident.boot == boot_now
            and row is not None
            and not row.zombie
            and row.start == ident.start
        )

    if live_match(leader):
        return RecoveryDecision("iii", "recovery", None, idents, leader)
    group_gone = leader.boot != boot_now or not any(
        row.pgid == leader.group and not row.zombie for row in table.values()
    )
    rows_gone = not any(live_match(ident) for ident in idents)
    return RecoveryDecision("ii", "recovery", group_gone and rows_gone, idents, leader)


def stop_recovered(
    decision: RecoveryDecision,
    *,
    record: Callable[[Identity], None],
    source: ProcessSource | None = None,
    signaller: Signaller | None = None,
) -> GroupStop:
    """Branch (iii)'s stop: start-guard every recorded row (a live pid with another start is
    reused and never signalled), then stop what is attributable, and its live descendants, through
    the one stopper as B2-C10 does. A process found now that has no row gets one first."""
    assert decision.branch == "iii" and decision.leader is not None
    attribution = Attribution(
        group=decision.leader.group,
        record=record,
        source=source,
        known=list(decision.identities),
    )
    stop = stop_group(attribution, signaller=signaller)
    attribution.close()
    return stop
