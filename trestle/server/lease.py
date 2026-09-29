"""The environment lease's definition and its holder index (WR-OWN-8, L.SL-8.1).

A run holds the lease on an environment from its `created` row until its terminal row or its
admitted deadline, whichever comes first. The lease is *defined over the ledger*: there is no
lease store file. The environment key is the canonical JSON of the request argument the plugin
names in `env_arg` (opaque to the host: two requests share an environment exactly when the bytes
are equal); admission records it in the `created` row (`lease_key`) and in the plan's
`lease_set`. `Holders` is an in-memory index of the runs that may hold a lease, rebuilt from the
ledgers at startup by `rebuild_holders`; whether a run *still* holds it is always decided by
`holder_of`, which reads the ledger, so a stale index entry can never over-hold. L.SL-8.2 queues
and refuses on this index and ends a lease by `release` at the terminal row.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trestle.common.plan.declared import canonical_json
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path

LEASE_KEY_FIELD = "lease_key"


def request_key(env_arg: str | None, args: Mapping[str, Any]) -> str | None:
    """The environment key of a request: the canonical JSON of the argument named by the plugin's
    `env_arg`, or None when the plugin declares no environment or the request gives no value for
    it (nothing is then held)."""
    if env_arg is None:
        return None
    value = args.get(env_arg)
    if value is None:
        return None
    return canonical_json(value)


@dataclass(frozen=True)
class Holder:
    """One run's claim on an environment key; `deadline_epoch` is its admitted deadline (wall
    clock, seconds since the epoch)."""

    run_id: str
    key: str
    deadline_epoch: float


def deadline_epoch(run_dir: Path) -> float | None:
    """The run's admitted deadline from its spec (`evidence/spec.json`), or None when unreadable."""
    try:
        spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
        fixed = datetime.fromisoformat(str(spec["deadline"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if fixed.tzinfo is None:
        fixed = fixed.replace(tzinfo=UTC)
    return fixed.timestamp()


def holder_of(run_dir: Path, *, now: float | None = None) -> Holder | None:
    """The lease definition over one run's ledger: a `created` row carrying a `lease_key`, no
    terminal row, and an admitted deadline not yet passed. None otherwise (including a run with
    no readable ledger or spec)."""
    path = ledger_path(run_dir)
    if not path.is_file():
        return None
    ledger = RunLedger.open(path)
    created = ledger.last_kind("created")
    if created is None or ledger.terminal_state() is not None:
        return None
    key = created.get(LEASE_KEY_FIELD)
    if not isinstance(key, str):
        return None
    deadline = deadline_epoch(run_dir)
    if deadline is None or (time.time() if now is None else now) >= deadline:
        return None
    return Holder(run_id=str(created.get("run_id", run_dir.name)), key=key, deadline_epoch=deadline)


@dataclass
class Holders:
    """The index of runs that may hold a lease, by environment key, in admission order. Entries
    are candidates: `held` applies the ledger definition to each."""

    _entries: dict[str, tuple[Holder, Path]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, holder: Holder, run_dir: Path) -> None:
        with self._lock:
            self._entries[holder.run_id] = (holder, run_dir)

    def release(self, run_id: str) -> None:
        """Drop a run from the index (the lease ended at its terminal row)."""
        with self._lock:
            self._entries.pop(run_id, None)

    def held(self, key: str | None = None, *, now: float | None = None) -> tuple[Holder, ...]:
        """The runs that hold a lease now, oldest admission first, optionally of one key. Each
        candidate is judged by `holder_of` (ledger, deadline); the index itself is not changed."""
        with self._lock:
            candidates = list(self._entries.values())
        return tuple(
            holder
            for holder, run_dir in candidates
            if (key is None or holder.key == key) and holder_of(run_dir, now=now) is not None
        )

    def prune(self, *, now: float | None = None) -> None:
        """Drop the runs whose lease has ended (terminal row or deadline) from the index."""
        with self._lock:
            candidates = list(self._entries.values())
        for holder, run_dir in candidates:
            if holder_of(run_dir, now=now) is None:
                self.release(holder.run_id)

    def snapshot(self, *, now: float | None = None) -> frozenset[Holder]:
        """Every current holder, as a set (the comparand of a restart's rebuild)."""
        return frozenset(self.held(now=now))


def rebuild_holders(home: Path, *, now: float | None = None) -> Holders:
    """Rebuild the holder index from the ledgers under `home` (server startup, after recovery):
    every run whose ledger holds the lease per `holder_of`, in run-directory order. Reads the
    ledgers only; nothing is written."""
    holders = Holders()
    runs_root = home / "runs"
    if not runs_root.is_dir():
        return holders
    for month_dir in sorted(runs_root.iterdir()):
        if not month_dir.is_dir():
            continue
        for run_dir in sorted(month_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            holder = holder_of(run_dir, now=now)
            if holder is not None:
                holders.add(holder, run_dir)
    return holders
