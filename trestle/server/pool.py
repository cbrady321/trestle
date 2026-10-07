"""The home's one slot pool (v0.4 Problem A, rules 5, 6 and 8): `home/sched.json`.

The home has one pool of `max_running_runs` slots, shared by every server on it. A run holds a
slot from its grant until its owner's `Scheduler.complete`. `home/sched.json` is one small file,
rewritten (`atomic_write`) only under the admission lock, that caches what every check needs from
the live markers: each server's row (`last_pass_at` from its 250 ms pass, `seen_at` from its 10 s
reaper pass, `last_grant_at`, `draining`, `keyless_waiting`), every running run (its server, None
for a v0.3.0 run, its environment key and deadline) and every queued run with an environment key,
oldest first. It is rebuilt from `home/live/` when missing or unreadable; the markers stay the
reaper's index.

Each server grants only its own runs (it must spawn them), in its own FIFO order:

- Candidates. A server competes while it has a startable waiting run, its server lock is held and
  its `last_pass_at` is under 1 s old; a server at `[operator] max_share` is not one.
- Order. Candidates rank by slots held, fewest first, then oldest last grant (never granted is
  oldest). Server S takes a free slot when `free > ahead + idle`: `ahead` counts the candidates
  ranked before S, `idle` the other servers owed a reserve slot.
- Reserve. A server that holds no slot, has nothing waiting, is not draining, holds its server
  lock and was seen by its own reaper under 30 s ago is owed one free slot, so its first run starts
  at once. The reserve is capped at `max_running_runs - 1` slots, so it never takes
  the last one.
- Environment keys. A run with a key starts only when no running run (any server, v0.3.0 runs
  included) holds the key and no older run with the key waits on a candidate server (arrival order
  is created.at, then run id); checked in the same locked step as the grant and the marker flip.

A server whose lock is free is dropped from the rows (with its queued entries: they never start);
its running runs keep their slots and keys until the reaper finalizes each one.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from trestle.common.fsutil import atomic_write
from trestle.common.ids import run_id_ms
from trestle.server.home import (
    is_locked,
    live_run_ids,
    marked_run_dir,
    marker_path,
    read_marker,
    servers_dir,
    write_marker,
)
from trestle.server.runs import cancel_flag_path

SCHED_FILE = "sched.json"
# Rule 5's periods. Module constants, read at each use, so a test can shorten them.
PASS_INTERVAL_S = 0.25  # the pass while this server has waiting (or held) runs
PASS_STALE_S = 0.5  # a pass takes the lock when its own last_pass_at is older than this
CANDIDATE_FRESH_S = 1.0  # last_pass_at under this: a slot candidate
RESERVE_FRESH_S = 30.0  # seen_at under this: owed a reserve slot

State = dict[str, Any]


@dataclass(frozen=True)
class Want:
    """One of this server's waiting runs, in its FIFO order: what a grant may start."""

    run_id: str
    key: str | None
    arrival: float
    deadline: float


def sched_path(home: Path) -> Path:
    return home / SCHED_FILE


def empty_state() -> State:
    return {"servers": {}, "running": {}, "waiting_keys": {}}


def new_row() -> dict[str, Any]:
    return {
        "last_pass_at": 0.0,
        "seen_at": 0.0,
        "last_grant_at": None,
        "draining": False,
        "keyless_waiting": 0,
    }


def epoch(value: object, default: float = 0.0) -> float:
    """A marker's time (epoch seconds, or an ISO timestamp such as `created.at`), epoch seconds."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return default
    return default


def arrival_order(arrival: object, run_id: str) -> tuple[float, int, str]:
    """Rule 6's arrival order: created.at, then the run id's own timestamp, then the id."""
    return (epoch(arrival), run_id_ms(run_id), run_id)


# --- the file ------------------------------------------------------------------------------------


def read_sched(home: Path) -> State | None:
    """`home/sched.json` as written, or None when missing, unreadable or malformed. Lock-free: the
    file is only ever replaced whole, so a reader sees one complete write."""
    try:
        loaded = json.loads(sched_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    if not all(isinstance(loaded.get(name), dict) for name in ("servers", "running")):
        return None
    if not isinstance(loaded.get("waiting_keys", {}), dict):
        return None
    loaded.setdefault("waiting_keys", {})
    return loaded


def rebuild_sched(home: Path) -> State:
    """The scheduling facts from the live markers (rule 7 step 1, when the file is missing or
    unreadable). Server rows start stale (no pass, no sighting): each server's next pass
    refreshes its own."""
    state = empty_state()
    for run_id in live_run_ids(home):
        marker = read_marker(home, run_id)
        if marker is None:
            continue
        owner = marker.get("owner")
        key = marker.get("lease_key") if isinstance(marker.get("lease_key"), str) else None
        deadline = epoch(marker.get("deadline"), math.inf)
        if not isinstance(owner, str):
            # a v0.3.0 run: no slot, but its key is held until the reaper ends it (Old runs)
            state["running"][run_id] = {"server": None, "key": key, "deadline": deadline}
            continue
        row = state["servers"].setdefault(owner, new_row())
        if marker.get("state") == "running":
            state["running"][run_id] = {"server": owner, "key": key, "deadline": deadline}
        elif marker.get("state") == "queued":
            if key is None:
                row["keyless_waiting"] += 1
            else:
                _add_keyed(state, owner, run_id, key, epoch(marker.get("arrival")), deadline)
    return state


def load_sched(home: Path) -> State:
    """Rule 7 step 1 (under the admission lock): the file, else rebuilt from the markers."""
    state = read_sched(home)
    return state if state is not None else rebuild_sched(home)


def write_sched(home: Path, state: State) -> None:
    """Rule 7 step 7 (under the admission lock)."""
    data = json.dumps(state, sort_keys=True, separators=(",", ":"), default=_json_default)
    atomic_write(sched_path(home), data.encode("utf-8"))


def _json_default(value: object) -> object:
    raise TypeError(f"not JSON: {value!r}")


# --- the facts -----------------------------------------------------------------------------------


def server_lock_path(home: Path, server_id: str) -> Path:
    return servers_dir(home) / f"{server_id}.lock"


def slots_of(state: State, server_id: str) -> int:
    return sum(1 for entry in state["running"].values() if entry.get("server") == server_id)


def used_slots(state: State) -> int:
    """Slots held: every running run but a v0.3.0 one (it takes no server's slot)."""
    return sum(1 for entry in state["running"].values() if entry.get("server") is not None)


def running_keys(state: State) -> set[str]:
    return {str(entry["key"]) for entry in state["running"].values() if entry.get("key")}


def has_waiting(state: State, server_id: str) -> bool:
    row = state["servers"].get(server_id) or {}
    if int(row.get("keyless_waiting") or 0) > 0:
        return True
    return any(
        item.get("server") == server_id
        for items in state["waiting_keys"].values()
        for item in items
    )


def _add_keyed(
    state: State, server_id: str, run_id: str, key: str, arrival: float, deadline: float
) -> None:
    items = state["waiting_keys"].setdefault(key, [])
    if any(item.get("run_id") == run_id for item in items):
        return
    items.append({"run_id": run_id, "server": server_id, "arrival": arrival, "deadline": deadline})
    items.sort(key=lambda item: arrival_order(item.get("arrival"), str(item.get("run_id"))))


def add_waiting(
    state: State,
    server_id: str,
    run_id: str,
    key: str | None,
    arrival: float,
    deadline: float,
) -> None:
    """Rule 7 step 5: a new waiting run of `server_id`."""
    row = state["servers"].setdefault(server_id, new_row())
    if key is None:
        row["keyless_waiting"] = int(row.get("keyless_waiting") or 0) + 1
    else:
        _add_keyed(state, server_id, run_id, key, arrival, deadline)


def forget(state: State, run_id: str) -> None:
    """A completion or a reap: the run leaves the pool (its slot and its key are free)."""
    state["running"].pop(run_id, None)
    forget_waiting(state, run_id)


def drop_dead_servers(home: Path, state: State, me: str | None) -> list[str]:
    """Rule 7 step 1: drop the row (and queued entries) of every other server whose lock is free.
    Its running runs keep their slots and keys until the reaper ends each one."""
    dropped = [
        server_id
        for server_id in state["servers"]
        if server_id != me and not is_locked(server_lock_path(home, server_id))
    ]
    for server_id in dropped:
        del state["servers"][server_id]
    if dropped:
        gone = set(dropped)
        for key in list(state["waiting_keys"]):
            items = [item for item in state["waiting_keys"][key] if item.get("server") not in gone]
            if items:
                state["waiting_keys"][key] = items
            else:
                del state["waiting_keys"][key]
    return dropped


def drop_unmarked(home: Path, state: State) -> None:
    """Rule 7 step 1: an entry whose live marker is gone is stale (a marker is written before its
    entry and removed, under the lock, with it), so a lost completion never keeps a slot."""
    for run_id in list(state["running"]):
        if not marker_path(home, run_id).exists():
            forget(state, run_id)
    for items in list(state["waiting_keys"].values()):
        for item in items:
            if not marker_path(home, str(item.get("run_id"))).exists():
                forget_waiting(state, str(item.get("run_id")))


def busy_until(state: State, key: str, *, now: float | None = None) -> float | None:
    """Rule 6's busy pre-check: the latest deadline among the key's queued and running runs (held
    runs are not listed), or None when none is still within its deadline."""
    at = time.time() if now is None else now
    deadlines = [
        float(entry.get("deadline") or 0.0)
        for entry in state["running"].values()
        if entry.get("key") == key
    ]
    deadlines += [float(item.get("deadline") or 0.0) for item in state["waiting_keys"].get(key, [])]
    live = [deadline for deadline in deadlines if deadline > at]
    return max(live) if live else None


def key_runs(
    state: State, key: str | None = None, *, now: float | None = None
) -> list[tuple[str, str]]:
    """`(run_id, key)` of every run that holds or waits on an environment key (`key`, or any) and
    is within its deadline: the running ones by run id, then each key's waiters, oldest arrival
    first. What the busy pre-check counts."""
    at = time.time() if now is None else now
    running = sorted(
        (run_id, str(entry["key"]))
        for run_id, entry in state["running"].items()
        if entry.get("key")
        and (key is None or entry["key"] == key)
        and float(entry.get("deadline") or 0.0) > at
    )
    waiting = [
        (str(item["run_id"]), name)
        for name, items in state["waiting_keys"].items()
        if key is None or name == key
        for item in items
        if float(item.get("deadline") or 0.0) > at
    ]
    return running + waiting


# --- one server's locked step --------------------------------------------------------------------


def sync_own(
    state: State,
    me: str,
    wants: list[Want],
    unqueued: set[str],
    *,
    draining: bool,
) -> dict[str, Any]:
    """This server's own facts from its memory: its keyed waiting entries are exactly its waiting
    runs (and the admitted runs not yet handed to its FIFO, which admission listed), its
    `keyless_waiting` the rest, its `draining` flag. Returns its row."""
    row: dict[str, Any] = state["servers"].setdefault(me, new_row())
    keep: set[str] = set()
    for key in list(state["waiting_keys"]):
        items = []
        for item in state["waiting_keys"][key]:
            if item.get("server") != me:
                items.append(item)
            elif item.get("run_id") in unqueued:
                items.append(item)
                keep.add(str(item["run_id"]))
        if items:
            state["waiting_keys"][key] = items
        else:
            del state["waiting_keys"][key]
    for want in wants:
        if want.key is not None:
            _add_keyed(state, me, want.run_id, want.key, want.arrival, want.deadline)
    row["keyless_waiting"] = sum(1 for want in wants if want.key is None) + len(unqueued - keep)
    row["draining"] = draining
    return row


def choose_grants(
    state: State,
    me: str,
    wants: list[Want],
    *,
    max_running: int,
    max_share: int | None,
    now: float,
) -> list[Want]:
    """Rules 5 and 6: which of this server's waiting runs (in FIFO order) start now. Every other
    row is a live server (`drop_dead_servers` ran first). Updates `state` for each grant: the run
    is running, holds its key and is no longer waiting; this server's `last_grant_at` is now."""
    servers: dict[str, dict[str, Any]] = state["servers"]
    servers.setdefault(me, new_row())
    fresh = {
        server_id
        for server_id, row in servers.items()
        if server_id == me or now - float(row.get("last_pass_at") or 0.0) < CANDIDATE_FRESH_S
    }
    held_keys = running_keys(state)

    def key_blocked(key: str | None, run_id: str, arrival: float) -> bool:
        if key is None:
            return False
        if key in held_keys:
            return True
        return any(
            item.get("server") in fresh
            and item.get("run_id") != run_id
            and arrival_order(item.get("arrival"), str(item.get("run_id")))
            < arrival_order(arrival, run_id)
            for item in state["waiting_keys"].get(key, [])
        )

    def startable(server_id: str) -> bool:
        if int(servers[server_id].get("keyless_waiting") or 0) > 0:
            return True
        return any(
            item.get("server") == server_id
            and not key_blocked(key, str(item.get("run_id")), float(item.get("arrival") or 0.0))
            for key, items in state["waiting_keys"].items()
            for item in items
        )

    def capped(server_id: str) -> bool:
        return max_share is not None and slots_of(state, server_id) >= max_share

    def rank(server_id: str) -> tuple[int, float, str]:
        last = servers[server_id].get("last_grant_at")
        return (slots_of(state, server_id), -math.inf if last is None else float(last), server_id)

    def owed_reserve(server_id: str) -> bool:
        row = servers[server_id]
        return (
            slots_of(state, server_id) == 0
            and not has_waiting(state, server_id)
            and not row.get("draining")
            and now - float(row.get("seen_at") or 0.0) < RESERVE_FRESH_S
        )

    granted: list[Want] = []
    while True:
        free = max_running - used_slots(state)
        if free <= 0 or capped(me):
            break
        pick = next(
            (
                want
                for want in wants
                if want not in granted and not key_blocked(want.key, want.run_id, want.arrival)
            ),
            None,
        )
        if pick is None:
            break
        others = [server_id for server_id in fresh if server_id != me]
        candidates = [s for s in others if not capped(s) and startable(s)]
        ahead = [s for s in candidates if rank(s) < rank(me)]
        idle = [s for s in servers if s != me and s not in candidates and owed_reserve(s)]
        # the reserve never takes the last slot: with a pool of 1, an idle server's reserve
        # would otherwise block every other server for good
        if free <= len(ahead) + min(len(idle), max_running - 1):
            break
        state["running"][pick.run_id] = {"server": me, "key": pick.key, "deadline": pick.deadline}
        forget_waiting(state, pick.run_id)
        if pick.key is not None:
            held_keys.add(pick.key)
        servers[me]["last_grant_at"] = now
        granted.append(pick)
    return granted


def forget_waiting(state: State, run_id: str) -> None:
    for key in list(state["waiting_keys"]):
        items = [item for item in state["waiting_keys"][key] if item.get("run_id") != run_id]
        if items:
            state["waiting_keys"][key] = items
        else:
            del state["waiting_keys"][key]


def flip_marker(home: Path, run_id: str, state_name: str) -> bool:
    """Rule 6: the marker's state changes with the grant, in the same locked step. False when the
    marker is gone (the run was finished meanwhile)."""
    marker = read_marker(home, run_id)
    if marker is None:
        return False
    if marker.get("state") != state_name:
        write_marker(home, run_id, {**marker, "state": state_name})
    return True


def release_marker(home: Path, run_id: str, deadline: float) -> bool:
    """Feature 3: a released run's marker goes held -> queued with the deadline its `released` row
    minted, so `home/sched.json` lists it from the same locked step. False when the marker is gone
    (the run was finished meanwhile)."""
    marker = read_marker(home, run_id)
    if marker is None:
        return False
    write_marker(home, run_id, {**marker, "state": "queued", "deadline": deadline})
    return True


# --- a server's handle on the pool ---------------------------------------------------------------


@dataclass
class Pool:
    """One server's view of the home's pool: its server id, the completions whose `sched.json`
    entry is still to be removed (a completion that found the admission lock busy leaves it to the
    next locked step) and the cancel flags of its waiting runs."""

    home: Path
    server_id: str
    _pending: set[str] = field(default_factory=set, repr=False)
    _flags: dict[str, Path] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def load(self) -> State:
        """Rule 7 step 1 under the admission lock: the file (or its rebuild), dead servers'
        rows dropped, and every pending completion applied."""
        state = load_sched(self.home)
        drop_dead_servers(self.home, state, self.server_id)
        drop_unmarked(self.home, state)
        with self._lock:
            pending, self._pending = self._pending, set()
        for run_id in pending:
            forget(state, run_id)
        return state

    def write(self, state: State) -> None:
        write_sched(self.home, state)

    def completed(self, run_id: str) -> None:
        """The run is done here: its entry goes at the next locked step (`settle`, or any)."""
        with self._lock:
            self._pending.add(run_id)
            self._flags.pop(run_id, None)

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def settle(self) -> None:
        """Under the admission lock (the completion's locked step): apply pending completions."""
        self.write(self.load())

    def cancel_requested(self, run_id: str) -> bool:
        """Whether a cancel flag was written for a waiting run (by any server's `cancel`)."""
        with self._lock:
            flag = self._flags.get(run_id)
        if flag is None:
            run_dir = marked_run_dir(self.home, run_id, read_marker(self.home, run_id))
            if run_dir is None:
                return False
            flag = cancel_flag_path(run_dir)
            with self._lock:
                self._flags[run_id] = flag
        return flag.exists()

    def pre_read(self) -> State | None:
        """The lock-free read a pass makes first (rule 5, When)."""
        return read_sched(self.home)
