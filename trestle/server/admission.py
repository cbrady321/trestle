"""Admit port — refuse or mint Handle."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trestle.common import clock, codes
from trestle.common.canonical import args_hash
from trestle.common.fsutil import atomic_write_json, fsync_dir
from trestle.common.ids import generate_run_id
from trestle.common.plan import carving, compiler
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.redact import redact_args, secret_values
from trestle.common.types import (
    AdmitRequest,
    AdmitResult,
    AdmitResultAdmitted,
    AdmitResultRefused,
    PluginSnapshot,
    RequestOutcome,
    RunSpec,
)
from trestle.server import lease
from trestle.server.config import ProfileConfig, load_config
from trestle.server.idempotency import IdempotencyStore
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, run_dir_for, work_dir
from trestle.server.plugin_paths import CATALOG_HINT_PACKS_MISSING
from trestle.server.plugin_schema import ArgsError, validate_args
from trestle.server.plugin_validate import validate_plugin_imports
from trestle.server.recovery import find_run_dir
from trestle.server.registry import Registry
from trestle.server.scheduler import Scheduler
from trestle.server.snapshots import (
    deadline_of,
    load_declared,
    load_declared_tree,
    load_snapshot_schema,
)

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
    # the runs that may hold an environment lease (WR-OWN-8), rebuilt from the ledgers at startup
    holders: lease.Holders = field(default_factory=lease.Holders)

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

        # B2-C2: every root is compiled and carved to a plan before any run id exists (a refusal
        # is not a run); a plan-less root gets the implicit depth-1 plan (B2-C1). The whole
        # declared tree is compiled here (MC-23 over MC-34), so a tree defective in any way the
        # declaration and the request show is refused with its own code naming the identifier.
        planned = plan_for_admission(snap, req, deadline_s)
        if isinstance(planned, AdmitResultRefused):
            return planned
        busy = self._environment_busy(planned, deadline_s)
        if busy is not None:
            return busy
        admitted = write_admitted_run(
            self.home, snap, req, planned, service_epoch=self.service_epoch
        )
        if admitted.lease_key is not None:
            self.holders.prune()
            self.holders.add(
                lease.Holder(admitted.run_id, admitted.lease_key, admitted.deadline_epoch),
                admitted.run_dir,
            )
        self.scheduler.mint(admitted.run_id, snap.snapshot_id, admitted.spec_hash)
        # the real values travel in memory to the run's WorkOrder and no further
        return AdmitResultAdmitted(tag="admitted", run_id=admitted.run_id, secrets=admitted.secrets)

    def _environment_busy(self, plan: AdmittedPlan, deadline_s: float) -> AdmitResultRefused | None:
        """B2 ordering step 2, the lease pre-check (WR-OWN-8, B2-C5): a request whose environment
        is held is queued FIFO within its deadline (`ControlSurface`), unless the holders'
        recorded deadlines leave it less than the plan's worst case plus release slice before its
        own would-be deadline: then it is refused `admission.environment_busy` (retryable) with no
        run id, since waiting could not end in a run that fits."""
        if not plan.lease_set:
            return None
        free_at = self.holders.latest_deadline(plan.lease_set[0])
        if free_at is None:
            return None
        worst_case = carving.worst_case_s(plan, clock.FINALIZATION_RESERVE_S)
        if not lease.leaves_too_little(
            free_at, time.time() + deadline_s, worst_case, plan.release_slice
        ):
            return None
        return AdmitResultRefused(
            tag="refused",
            outcome=RequestOutcome(
                code=codes.ADMISSION_ENVIRONMENT_BUSY,
                message=(
                    "the environment is held by a run whose deadline leaves this request too "
                    "little time; retry after it ends"
                ),
                retryable=True,
                origin="admission",
            ),
        )


def _plan_refusal(refusal: compiler.Refusal) -> AdmitResultRefused:
    """A plan refusal as the request's outcome: the plan code, naming the identifier (and, for an
    unknown identifier, where the valid ones are listed)."""
    parts = [refusal.message or refusal.code, f"({refusal.identifier})"]
    if refusal.valid_listed_at is not None:
        parts.append(f"valid identifiers are listed at {refusal.valid_listed_at}")
    return AdmitResultRefused(
        tag="refused",
        outcome=RequestOutcome(
            code=refusal.code,
            message=" ".join(parts)[: compiler.REFUSAL_TEXT_MAX],
            retryable=False,
            origin="admission",
        ),
    )


def plan_for_admission(
    snap: PluginSnapshot, req: AdmitRequest, deadline_s: float
) -> AdmittedPlan | AdmitResultRefused:
    """Compile and carve this request's plan (B2-C2, MC-23): the declared tree of a workflow
    snapshot (MC-34), or the implicit depth-1 plan of a plain plugin (B2-C1); the carve and this
    root's release slice attached, `plan_digest` over all of it. Pure over the snapshot and the
    request; a refusal names its node or identifier and no run id exists yet."""
    declared = load_declared_tree(snap)
    if declared is None:
        plan = compiler.implicit_depth1_plan(snap.plugin)
        # WR-OWN-8: a plain plugin naming an environment argument holds that environment's lease
        # (a tree's `lease_set` is compiled from its root's `env_key_field`, equal to `env_arg`)
        key = lease.request_key(load_declared(snap).env_arg, req.args)
        if key is not None:
            plan = replace(plan, lease_set=(key,))
    else:
        compiled = compiler.compile(declared, req.args)
        if isinstance(compiled, compiler.Refusal):
            return _plan_refusal(compiled)
        plan = compiled
    release_slice = carving.release_slice_for(plan, clock.release_slice)
    slices = carving.carve(
        plan,
        deadline_s,
        clock.FINALIZATION_RESERVE_S,
        release_slice,
        deadline_ceiling_s=clock.deadline_ceiling,
    )
    if isinstance(slices, compiler.Refusal):
        return _plan_refusal(slices)
    # B2-C2 (5): the finalization the host needs after the deadline must fit the margin
    misfit = carving.margin_misfit(
        plan,
        carving.MarginLimits(
            grace=clock.grace, kill=clock.kill, sweep_parallelism=clock.sweep_parallelism
        ),
        clock.finalization_margin,
    )
    if misfit is not None:
        return _plan_refusal(misfit)
    return carving.attach(plan, slices, release_slice)


@dataclass(frozen=True)
class AdmittedRun:
    """What `write_admitted_run` minted: the run id, the hash of its spec (the run's identity,
    which the plan digest is part of), and the real values of its declared secrets (in memory
    only, MC-CORE-13). `lease_key` is the environment key recorded in the `created` row (None when
    the run holds no lease) and `deadline_epoch` its admitted deadline, wall clock."""

    run_id: str
    spec_hash: str
    lease_key: str | None = None
    deadline_epoch: float = 0.0
    run_dir: Path = Path()
    secrets: dict[str, object] = field(default_factory=dict, repr=False, compare=False)


def write_admitted_run(
    home: Path,
    snap: PluginSnapshot,
    req: AdmitRequest,
    plan: AdmittedPlan,
    *,
    service_epoch: str = "",
) -> AdmittedRun:
    """The post-refusal half of admission (MC-B2-08): mint the run id, write the run directory,
    `spec.json` (with `plan`), the `created` row and the idempotency record. Every refusal has
    already happened; the harness's `run_tree` (L.SV-5.7) admits through here too."""
    a_hash = args_hash(req.args)
    deadline_s, _ = deadline_of(snap)
    run_id = generate_run_id()
    run_dir = run_dir_for(home, run_id)
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
        plan=json.loads(plan.to_json()),
    )
    spec_dict = spec.to_dict()
    atomic_write_json(ev_dir / "spec.json", spec_dict)
    # the plan digest is inside the spec, so it is part of the run's identity (design S-8)
    spec_hash = hashlib.sha256(
        json.dumps(spec_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    ledger = RunLedger.open(ledger_path(run_dir))
    created_fields: dict[str, object] = {
        "run_id": run_id,
        "spec_hash": spec_hash,
        "service_epoch": service_epoch,
        "plugin": snap.plugin,
        "version": snap.version,
        "snapshot_id": snap.snapshot_id,
        "args_hash": a_hash,
        "caller_session": req.caller_session,
    }
    if req.idempotency_key is not None:
        created_fields["idempotency_key"] = req.idempotency_key
    # WR-OWN-8: the environment key the run holds a lease on, when it holds one; absent otherwise,
    # so a run that declares no environment writes the same row as before
    lease_key = plan.lease_set[0] if plan.lease_set else None
    if lease_key is not None:
        created_fields[lease.LEASE_KEY_FIELD] = lease_key
    ledger.append("created", **created_fields)
    fsync_dir(run_dir)

    if req.idempotency_key is not None:
        cfg = load_config(home)
        IdempotencyStore.open(home).remember(
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

    return AdmittedRun(
        run_id=run_id,
        spec_hash=spec_hash,
        lease_key=lease_key,
        deadline_epoch=deadline.timestamp(),
        run_dir=run_dir,
        secrets=secret_values(req.args, declared.secrets),
    )
