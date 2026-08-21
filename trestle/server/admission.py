"""Admit port — refuse or mint Handle."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trestle.common import codes
from trestle.common.canonical import args_hash
from trestle.common.fsutil import atomic_write_json, fsync_dir
from trestle.common.ids import generate_run_id
from trestle.common.types import (
    AdmitRequest,
    AdmitResult,
    AdmitResultAdmitted,
    AdmitResultRefused,
    RequestOutcome,
    RunSpec,
)
from trestle.server.config import load_config
from trestle.server.idempotency import IdempotencyStore
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, run_dir_for, work_dir
from trestle.server.recovery import find_run_dir
from trestle.server.registry import Registry
from trestle.server.scheduler import Scheduler


@dataclass
class Admission:
    home: Path
    registry: Registry
    scheduler: Scheduler
    service_epoch: str

    def admit(self, req: AdmitRequest) -> AdmitResult:
        capacity = self.scheduler.check_admit_capacity()
        if capacity is not None:
            return capacity

        snap = self.registry.get(req.plugin)
        if snap is None:
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.PLUGIN_NOT_FOUND,
                    message=f"plugin not found: {req.plugin}",
                    retryable=False,
                    origin="admission",
                ),
            )

        if req.version is not None and req.version != snap.version:
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.PLUGIN_NOT_FOUND,
                    message=f"plugin version not found: {req.plugin}@{req.version}",
                    retryable=False,
                    origin="admission",
                ),
            )

        try:
            a_hash = args_hash(req.args)
        except (TypeError, ValueError) as exc:
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.INVALID_ARGS,
                    message=str(exc),
                    retryable=False,
                    origin="admission",
                ),
            )

        cfg = load_config(self.home)
        if req.idempotency_key is not None:
            store = IdempotencyStore.open(self.home)
            store.purge_expired()
            existing = store.lookup(req.idempotency_key)
            if existing is not None:
                if (
                    existing.plugin == snap.plugin
                    and existing.snapshot_id == snap.snapshot_id
                    and existing.args_hash == a_hash
                    and find_run_dir(self.home, existing.run_id) is not None
                ):
                    return AdmitResultAdmitted(
                        tag="admitted",
                        run_id=existing.run_id,
                        existing=True,
                    )
                return AdmitResultRefused(
                    tag="refused",
                    outcome=RequestOutcome(
                        code=codes.IDEMPOTENCY_KEY_CONFLICT,
                        message=f"idempotency key conflict: {req.idempotency_key}",
                        retryable=False,
                        origin="admission",
                    ),
                )

        run_id = generate_run_id()
        run_dir = run_dir_for(self.home, run_id)
        month_dir = run_dir.parent
        month_dir.mkdir(parents=True, exist_ok=True)
        fsync_dir(month_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        fsync_dir(run_dir)
        ev_dir = evidence_dir(run_dir)
        w_dir = work_dir(run_dir)
        ev_dir.mkdir(parents=True, exist_ok=True)
        w_dir.mkdir(parents=True, exist_ok=True)
        (w_dir / "tmp").mkdir(parents=True, exist_ok=True)
        (w_dir / "outputs").mkdir(parents=True, exist_ok=True)
        (w_dir / "artifact-staging").mkdir(parents=True, exist_ok=True)
        fsync_dir(run_dir)

        deadline = datetime.now(tz=UTC) + timedelta(seconds=snap.timeout_s)
        spec = RunSpec(
            plugin=snap.plugin,
            version=snap.version,
            snapshot_id=snap.snapshot_id,
            args=req.args,
            args_hash=a_hash,
            source_sha256=snap.source_sha256,
            schema_sha256=snap.schema_sha256,
            manifest_sha256=snap.manifest_sha256,
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            summary_budget=snap.summary_budget,
            timeout_s=snap.timeout_s,
            deadline=deadline.isoformat(),
        )
        spec_dict = spec.to_dict()
        atomic_write_json(ev_dir / "spec.json", spec_dict)
        spec_hash = hashlib.sha256(
            json.dumps(spec_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        ledger = RunLedger.open(ledger_path(run_dir))
        created_fields: dict[str, object] = {
            "run_id": run_id,
            "spec_hash": spec_hash,
            "service_epoch": self.service_epoch,
            "plugin": snap.plugin,
            "version": snap.version,
            "snapshot_id": snap.snapshot_id,
            "args_hash": a_hash,
        }
        if req.idempotency_key is not None:
            created_fields["idempotency_key"] = req.idempotency_key
        ledger.append("created", **created_fields)
        fsync_dir(run_dir)

        if req.idempotency_key is not None:
            IdempotencyStore.open(self.home).remember(
                req.idempotency_key,
                run_id=run_id,
                plugin=snap.plugin,
                snapshot_id=snap.snapshot_id,
                args_hash=a_hash,
                ttl_s=cfg.idempotency_ttl_s,
            )

        self.scheduler.mint(run_id, snap.snapshot_id, spec_hash)
        return AdmitResultAdmitted(tag="admitted", run_id=run_id)
