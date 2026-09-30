"""Admit port — refuse or mint Handle."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trestle.common import clock, codes
from trestle.common.canonical import args_hash
from trestle.common.fsutil import atomic_write_json, fsync_dir
from trestle.common.ids import generate_run_id
from trestle.common.redact import redact_args, secret_values
from trestle.common.types import (
    AdmitRequest,
    AdmitResult,
    AdmitResultAdmitted,
    AdmitResultRefused,
    RequestOutcome,
    RunSpec,
)
from trestle.server.config import ProfileConfig, load_config
from trestle.server.idempotency import IdempotencyStore
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, run_dir_for, work_dir
from trestle.server.plugin_paths import CATALOG_HINT_PACKS_MISSING
from trestle.server.plugin_schema import ArgsError, validate_args
from trestle.server.plugin_validate import validate_plugin_imports
from trestle.server.recovery import find_run_dir
from trestle.server.registry import Registry
from trestle.server.scheduler import Scheduler
from trestle.server.snapshots import deadline_of, load_declared, load_snapshot_schema

# K-1 (MC-CORE-12, OQ-1 recorded default): the same idempotency key joins the run it named even
# after the plugin was republished, and the key's window covers the run's whole life. A join then
# needs the same plugin, the same `args_hash` and the run present (the snapshot is not compared);
# the entry lives until the admitted deadline plus the finalization margin plus the ttl. Cleared,
# both revert to S0: an equal `snapshot_id` is required and the entry lives `ttl` from admission.
# The CK-1 decline patch (CM-7) flips this constant.
JOIN_ACROSS_REPUBLISH: bool = True


@dataclass
class Admission:
    home: Path
    registry: Registry
    scheduler: Scheduler
    service_epoch: str
    profile: ProfileConfig = ProfileConfig()

    def admit(self, req: AdmitRequest) -> AdmitResult:
        # The restricted profile's allowlist comes first: a refusal here mints nothing (no run id,
        # no run dir, no process) and does not depend on whether the plugin exists (WR-AUTH-2).
        if not self.profile.allows(req.plugin):
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.NOT_ALLOWLISTED,
                    message=f"plugin not allowlisted under the restricted profile: {req.plugin}",
                    retryable=False,
                    origin="admission",
                ),
            )
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

        import_error = validate_plugin_imports(Path(snap.source_path))
        if import_error is not None:
            message = f"plugin import failed: {import_error}"
            if "trestle_packs" in import_error:
                message = f"{message}; {CATALOG_HINT_PACKS_MISSING}"
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.IMPORT_FAILED,
                    message=message,
                    retryable=False,
                    origin="admission",
                ),
            )

        try:
            validate_args(req.args, load_snapshot_schema(snap))
            a_hash = args_hash(req.args)
        except ArgsError as exc:
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.INVALID_ARGS,
                    message=str(exc)[:200],
                    retryable=False,
                    origin="admission",
                ),
            )
        except (TypeError, ValueError) as exc:
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.INVALID_ARGS,
                    message=str(exc)[:200],
                    retryable=False,
                    origin="admission",
                ),
            )

        deadline_s, _ = deadline_of(snap)
        if deadline_s > clock.deadline_ceiling:
            # B2-C2 (4): the admitted deadline may not exceed the ceiling; nothing is minted
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.BUDGET_DOES_NOT_FIT,
                    message=(
                        f"declared deadline {deadline_s:g}s exceeds the ceiling "
                        f"{clock.deadline_ceiling:g}s"
                    ),
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
                    and (JOIN_ACROSS_REPUBLISH or existing.snapshot_id == snap.snapshot_id)
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

        deadline = datetime.now(tz=UTC) + timedelta(seconds=deadline_s)
        declared = load_declared(snap)
        spec = RunSpec(
            plugin=snap.plugin,
            version=snap.version,
            snapshot_id=snap.snapshot_id,
            # declared secrets are redacted (MC-CORE-13); args_hash above is over the real intent
            args=redact_args(req.args, declared.secrets),
            args_hash=a_hash,
            source_sha256=snap.source_sha256,
            schema_sha256=snap.schema_sha256,
            manifest_sha256=snap.manifest_sha256,
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            summary_budget=snap.summary_budget,
            timeout_s=math.ceil(deadline_s),
            deadline=deadline.isoformat(),
            # what publication recorded for the declared packages; the child checks it first
            provenance={"packages": dict(declared.package_digests)},
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
            "caller_session": req.caller_session,
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
                ttl_s=cfg.idempotency_ttl_s
                + (
                    math.ceil(snap.timeout_s + clock.finalization_margin)
                    if JOIN_ACROSS_REPUBLISH
                    else 0
                ),
            )

        self.scheduler.mint(run_id, snap.snapshot_id, spec_hash)
        # the real values travel in memory to the run's WorkOrder and no further
        return AdmitResultAdmitted(
            tag="admitted", run_id=run_id, secrets=secret_values(req.args, declared.secrets)
        )
