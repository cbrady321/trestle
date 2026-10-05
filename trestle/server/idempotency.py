"""Idempotency key store (R-WAIT-10–13)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from trestle.common.fsutil import atomic_write_json
from trestle.server.ledger import RunLedger, ledger_path


@dataclass(frozen=True)
class IdempotencyRecord:
    run_id: str
    plugin: str
    snapshot_id: str
    args_hash: str
    expires_at: float


@dataclass
class IdempotencyStore:
    home: Path
    entries: dict[str, IdempotencyRecord]

    @classmethod
    def open(cls, home: Path) -> IdempotencyStore:
        path = home / "idempotency.json"
        if not path.exists():
            return cls(home=home, entries={})
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("entries", {})
        entries: dict[str, IdempotencyRecord] = {}
        if isinstance(items, dict):
            for key, value in items.items():
                if not isinstance(value, dict):
                    continue
                entries[str(key)] = IdempotencyRecord(
                    run_id=str(value.get("run_id", "")),
                    plugin=str(value.get("plugin", "")),
                    snapshot_id=str(value.get("snapshot_id", "")),
                    args_hash=str(value.get("args_hash", "")),
                    expires_at=float(value.get("expires_at", 0.0)),
                )
        return cls(home=home, entries=entries)

    def save(self) -> None:
        payload = {
            "entries": {
                key: {
                    "run_id": record.run_id,
                    "plugin": record.plugin,
                    "snapshot_id": record.snapshot_id,
                    "args_hash": record.args_hash,
                    "expires_at": record.expires_at,
                }
                for key, record in self.entries.items()
            }
        }
        atomic_write_json(self.home / "idempotency.json", payload)

    def purge_expired(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        before = len(self.entries)
        self.entries = {
            key: record for key, record in self.entries.items() if record.expires_at > current
        }
        if len(self.entries) != before:
            self.save()

    def lookup(self, key: str) -> IdempotencyRecord | None:
        record = self.entries.get(key)
        if record is None:
            return None
        if record.expires_at <= time.time():
            del self.entries[key]
            self.save()
            return None
        return record

    def remember(
        self,
        key: str,
        *,
        run_id: str,
        plugin: str,
        snapshot_id: str,
        args_hash: str,
        ttl_s: int,
    ) -> None:
        self.entries[key] = IdempotencyRecord(
            run_id=run_id,
            plugin=plugin,
            snapshot_id=snapshot_id,
            args_hash=args_hash,
            expires_at=time.time() + ttl_s,
        )
        self.save()


def rebuild_from_ledgers(home: Path, *, ttl_s: int) -> None:
    """Reconstruct idempotency entries from durable ledger rows (R-WAIT-13)."""
    store = IdempotencyStore.open(home)
    runs_root = home / "runs"
    if not runs_root.exists():
        return
    changed = False
    for month_dir in runs_root.iterdir():
        if not month_dir.is_dir():
            continue
        for run_dir in month_dir.iterdir():
            if not run_dir.is_dir():
                continue
            path = ledger_path(run_dir)
            if not path.exists():
                continue
            ledger = RunLedger.open(path)
            created = ledger.last_kind("created")
            if created is None:
                continue
            key = created.get("idempotency_key")
            if not isinstance(key, str) or not key:
                continue
            if key in store.entries:
                continue
            store.entries[key] = IdempotencyRecord(
                run_id=str(created.get("run_id", run_dir.name)),
                plugin=str(created.get("plugin", "")),
                snapshot_id=str(created.get("snapshot_id", "")),
                args_hash=str(created.get("args_hash", "")),
                expires_at=time.time() + ttl_s,
            )
            changed = True
    if changed:
        store.save()
