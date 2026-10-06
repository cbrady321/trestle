"""Idempotency keys (R-WAIT-10–13; v0.4 Problem B): one file per key, `home/keys/<sha256>.json`.

A key file holds the key and every run that used it, newest first. Each entry records what a join
compares (`plugin`, `args_hash`, `call_deadline_s`, the call's own deadline argument or null, and
`after`), the run's effective `deadline_s` (recorded, not compared), whether its plugin declared
`repeatable`, and `key_expires_at`, fixed once at admission and recorded in the `created` row
(`admitted_at + deadline_s + finalization_margin + idempotency_ttl_s`). The files are a cache of
the ledgers that never revives an expired key: they are written only under the admission lock (an
admission's claim, GC's removal of a run, the reaper's purge, and the repair), and read lock-free.

Rebuilding is a repair only (`trestle doctor --rebuild-keys`, `trestle init --upgrade`): it replays
the `created` rows by created.at then run id, one key at a time under the admission lock, and
rewrites only key files that differ. A v0.3.0 row without `key_expires_at` takes the expiry
`idempotency.json` recorded for it, else v0.3.0's own formula; an expired key stays expired.
`idempotency.json` is read only by that upgrade.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trestle.common import clock
from trestle.common.fsutil import atomic_write_json
from trestle.server.home import HomeBusy, admission_lock
from trestle.server.ledger import RunLedger, iter_run_dirs, ledger_path
from trestle.server.recovery import find_run_dir

KEYS_DIR = "keys"


def now() -> float:
    """The wall clock every key decision reads (a test moves it)."""
    return time.time()


LEGACY_STORE = "idempotency.json"
# v0.3.0's key window used the 300 s snapshot default, whatever the run's deadline
LEGACY_DEADLINE_S = 300


@dataclass(frozen=True)
class KeyEntry:
    """One run that used a key."""

    run_id: str
    plugin: str
    args_hash: str
    key_expires_at: float
    deadline_s: float | None = None
    # the call's own `deadline_s` argument (Feature 0), None when it was omitted
    call_deadline_s: float | None = None
    # the call's `after` (Feature 3), canonical JSON, None when it was omitted
    after: str | None = None
    repeatable: bool = False
    retry_of: str | None = None

    def same_call(
        self, *, plugin: str, args_hash: str, call_deadline_s: float | None, after: str | None
    ) -> bool:
        """The join rule's identity: plugin, args_hash, the call's deadline argument and after
        (the effective deadline is not compared, so a republish never makes a conflict)."""
        return (
            self.plugin == plugin
            and self.args_hash == args_hash
            and self.call_deadline_s == call_deadline_s
            and self.after == after
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "plugin": self.plugin,
            "args_hash": self.args_hash,
            "after": self.after,
            "call_deadline_s": self.call_deadline_s,
            "deadline_s": self.deadline_s,
            "repeatable": self.repeatable,
            "key_expires_at": self.key_expires_at,
        }
        if self.retry_of is not None:
            out["retry_of"] = self.retry_of
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> KeyEntry | None:
        try:
            run_id = str(raw["run_id"])
            expires = float(raw["key_expires_at"])
        except (KeyError, TypeError, ValueError):
            return None
        return cls(
            run_id=run_id,
            plugin=str(raw.get("plugin", "")),
            args_hash=str(raw.get("args_hash", "")),
            key_expires_at=expires,
            deadline_s=_number(raw.get("deadline_s")),
            call_deadline_s=_number(raw.get("call_deadline_s")),
            after=raw["after"] if isinstance(raw.get("after"), str) else None,
            repeatable=raw.get("repeatable") is True,
            retry_of=raw["retry_of"] if isinstance(raw.get("retry_of"), str) else None,
        )

    @classmethod
    def from_created(cls, created: dict[str, Any], key_expires_at: float) -> KeyEntry:
        """The entry a `created` row records (the repair's source)."""
        return cls(
            run_id=str(created.get("run_id", "")),
            plugin=str(created.get("plugin", "")),
            args_hash=str(created.get("args_hash", "")),
            key_expires_at=key_expires_at,
            deadline_s=_number(created.get("deadline_s")),
            call_deadline_s=_number(created.get("call_deadline_s")),
            after=created["after"] if isinstance(created.get("after"), str) else None,
            repeatable=created.get("repeatable") is True,
            retry_of=created["retry_of"] if isinstance(created.get("retry_of"), str) else None,
        )


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


# --- the files -----------------------------------------------------------------------------------


def keys_dir(home: Path) -> Path:
    return home / KEYS_DIR


def key_path(home: Path, key: str) -> Path:
    return keys_dir(home) / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def read_entries(home: Path, key: str) -> list[KeyEntry]:
    """The runs that used `key`, newest first ([] for an unknown key or an unreadable file).
    Lock-free: a key file is replaced by atomic rename."""
    return _read_file(key_path(home, key))[1]


def _read_file(path: Path) -> tuple[str | None, list[KeyEntry]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    if not isinstance(raw, dict):
        return None, []
    runs = raw.get("runs")
    entries = [
        entry
        for item in (runs if isinstance(runs, list) else [])
        if isinstance(item, dict) and (entry := KeyEntry.from_dict(item)) is not None
    ]
    key = raw.get("key")
    return (key if isinstance(key, str) else None), entries


def write_entries(home: Path, key: str, entries: list[KeyEntry]) -> None:
    """Replace `key`'s file (newest first); an empty list removes it. The caller holds the
    admission lock."""
    path = key_path(home, key)
    if not entries:
        path.unlink(missing_ok=True)
        return
    atomic_write_json(path, {"key": key, "runs": [entry.to_dict() for entry in entries]})


def run_exists(home: Path, run_id: str) -> bool:
    return find_run_dir(home, run_id) is not None


def claim(home: Path, key: str, entry: KeyEntry) -> None:
    """Admission's claim (rule 7 step 2, written in step 4 (c) before the commit rename): `entry`
    becomes the key's newest run. An entry whose run directory is missing (an admission that died
    before its rename, or a run GC removed) is dropped: under the lock nothing else is in flight,
    and such an entry is free, never a conflict."""
    kept = [old for old in read_entries(home, key) if run_exists(home, old.run_id)]
    write_entries(home, key, [entry, *kept])


def lookup(home: Path, key: str, *, at_time: float | None = None) -> KeyEntry | None:
    """The run a re-send of `key` would join now: the newest entry while its `key_expires_at` is
    in the future and its run directory exists, else None. Never writes."""
    entries = read_entries(home, key)
    if not entries:
        return None
    newest = entries[0]
    at = now() if at_time is None else at_time
    if newest.key_expires_at <= at or not run_exists(home, newest.run_id):
        return None
    return newest


def forget_run(home: Path, key: str, run_id: str) -> None:
    """GC removed `run_id`: its entry leaves the key's file (under the admission lock; a busy lock
    leaves it, free since its run is gone, to the reaper's purge)."""
    with contextlib.suppress(HomeBusy, OSError), admission_lock(home):
        entries = read_entries(home, key)
        kept = [entry for entry in entries if entry.run_id != run_id]
        if len(kept) != len(entries):
            write_entries(home, key, kept)


def _key_files(home: Path) -> Iterator[Path]:
    root = keys_dir(home)
    if not root.is_dir():
        return
    for path in sorted(root.iterdir()):
        if path.suffix == ".json" and not path.name.startswith("."):
            yield path


def purge(home: Path, *, at_time: float | None = None) -> int:
    """The reaper's 10 s pass: drop the expired entries whose run is gone (an admission that died
    before its rename, a run GC removed), and key files left empty. Expired entries of runs that
    still exist stay, as history (Feature 1), until GC removes their run. Read lock-free; a file
    that needs a change is re-read and rewritten under the admission lock. Returns the entries
    dropped (a busy lock leaves them to the next pass)."""
    at = now() if at_time is None else at_time

    def stale(entry: KeyEntry) -> bool:
        return entry.key_expires_at <= at and not run_exists(home, entry.run_id)

    pending = []
    for path in _key_files(home):
        key, entries = _read_file(path)
        if key is not None and (not entries or any(stale(entry) for entry in entries)):
            pending.append(key)
    if not pending:
        return 0
    dropped = 0
    try:
        with admission_lock(home):
            for key in pending:
                entries = read_entries(home, key)
                kept = [entry for entry in entries if not stale(entry)]
                if len(kept) != len(entries) or not kept:
                    write_entries(home, key, kept)
                    dropped += len(entries) - len(kept)
    except (HomeBusy, OSError):  # the next pass tries again
        return dropped
    return dropped


# --- the repair ----------------------------------------------------------------------------------


def legacy_expiries(home: Path) -> dict[str, tuple[str, float]]:
    """What v0.3.0's `idempotency.json` recorded: key -> (run id, expires_at). Read only by the
    upgrade's rebuild."""
    try:
        raw = json.loads((home / LEGACY_STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = raw.get("entries") if isinstance(raw, dict) else None
    found: dict[str, tuple[str, float]] = {}
    for key, value in (items if isinstance(items, dict) else {}).items():
        if not isinstance(value, dict):
            continue
        try:
            found[str(key)] = (str(value.get("run_id", "")), float(value["expires_at"]))
        except (KeyError, TypeError, ValueError):
            continue
    return found


def legacy_expires_at(created_at: float, ttl_s: float) -> float:
    """v0.3.0's own formula: created.at + idempotency_ttl_s + ceil(300 + finalization_margin)."""
    return created_at + ttl_s + math.ceil(LEGACY_DEADLINE_S + clock.finalization_margin)


def rebuild_keys(home: Path, *, ttl_s: float, legacy: bool = False, lock: bool = True) -> int:
    """The repair (`trestle doctor --rebuild-keys`, and `trestle init --upgrade` with `legacy`):
    replay every `created` row that names a key, by created.at then run id, and rewrite each key
    file whose entries differ, one key at a time under the admission lock (`lock=False`: the
    caller already holds it). A row without `key_expires_at` (v0.3.0) takes the expiry
    `idempotency.json` recorded for that run (`legacy`), else v0.3.0's formula with `ttl_s`.
    An expired key stays expired. Returns the key files rewritten."""
    from trestle.server.pool import arrival_order, epoch

    recorded = legacy_expiries(home) if legacy else {}
    rows: list[tuple[tuple[float, int, str], str, KeyEntry]] = []
    for run_dir in iter_run_dirs(home):  # dot names (an admission in flight) are skipped
        path = ledger_path(run_dir)
        if not path.exists():
            continue
        created = RunLedger.open(path).last_kind("created")
        key = created.get("idempotency_key") if created is not None else None
        if created is None or not isinstance(key, str) or not key:
            continue
        run_id = str(created.get("run_id", run_dir.name))
        expires = _number(created.get("key_expires_at"))
        if expires is None:
            old = recorded.get(key)
            if old is not None and old[0] == run_id:
                expires = old[1]
            else:
                expires = legacy_expires_at(epoch(created.get("at")), ttl_s)
        entry = KeyEntry.from_created({**created, "run_id": run_id}, expires)
        rows.append((arrival_order(created.get("at"), run_id), key, entry))
    rows.sort(key=lambda row: row[0])
    by_key: dict[str, list[KeyEntry]] = {}
    for _, key, entry in rows:
        by_key.setdefault(key, []).insert(0, entry)  # newest first
    rewritten = 0
    for key, entries in by_key.items():
        with admission_lock(home) if lock else contextlib.nullcontext():
            if read_entries(home, key) != entries:
                write_entries(home, key, entries)
                rewritten += 1
    return rewritten
