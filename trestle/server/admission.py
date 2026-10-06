"""Admit port — refuse or mint Handle."""

from __future__ import annotations

import hashlib
import json
import math
import os
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
from trestle.server import idempotency as keys
from trestle.server import lease
from trestle.server import pool as pools
from trestle.server.config import ProfileConfig, load_config
from trestle.server.home import (
    ADMISSION_WAIT_S,
    ADMITTING_PREFIX,
    HomeBusy,
    Ownership,
    admission_lock,
    owner_lock_path,
    try_lock,
    write_marker,
)
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, run_dir_for, work_dir
from trestle.server.plugin_paths import CATALOG_HINT_PACKS_MISSING
from trestle.server.plugin_schema import ArgsError, validate_args
from trestle.server.plugin_validate import validate_plugin_imports
from trestle.server.recovery import find_run_dir
from trestle.server.registry import Registry
from trestle.server.runstate import trusted_state
from trestle.server.scheduler import Scheduler
from trestle.server.snapshots import (
    deadline_of,
    load_declared,
    load_declared_tree,
    load_snapshot_schema,
)

# K-1 (MC-CORE-12, OQ-1 recorded default): the same idempotency key joins the run it named even
# after the plugin was republished, and the key's window covers the run's whole life. A join needs
# the same plugin, the same `args_hash`, the same call deadline argument and `after`, and the run
# present (the snapshot is not compared); the entry lives `key_expires_at - admitted_at =
# deadline_s + finalization_margin + ttl` (v0.4 "Fix first"), `deadline_s` being the run's own
# deadline (`deadline_of`), never the snapshot default. v0.4 Problem B: `key_expires_at` is fixed
# here and recorded in `created` and `home/keys/<sha256(key)>.json`.


@dataclass(frozen=True)
class KeyCall:
    """What a keyed call is joined on (Problem B's join rule), beside its key: the plugin, the
    hash of the real arguments, the call's own `deadline_s` argument and its `after`."""

    plugin: str
    args_hash: str
    # Feature 0 seam: the call's `deadline_s` argument (None: omitted, the declaration applies)
    call_deadline_s: float | None = None
    # Feature 3 seam: the call's `after`, canonical JSON (None: omitted)
    after: str | None = None


def key_call(snap: PluginSnapshot, req: AdmitRequest, a_hash: str) -> KeyCall:
    """The call's join identity. Features 0 and 3 fill `call_deadline_s` and `after` from `req`."""
    return KeyCall(plugin=snap.plugin, args_hash=a_hash)


@dataclass(frozen=True)
class KeyClaim:
    """The key step's answer when the call starts a run: it claims the key (written in step 4
    (c)); `retry_of` names the interrupted run of a repeatable plugin it replaces."""

    retry_of: str | None = None


def _home_busy(busy: HomeBusy) -> AdmitResultRefused:
    return AdmitResultRefused(
        tag="refused",
        outcome=RequestOutcome(
            code=codes.ADMISSION_HOME_BUSY,
            message=(
                f"the home's admission lock stayed held past {ADMISSION_WAIT_S:g}s "
                f"({busy.holder or 'another process'}); retry"
            ),
            retryable=True,
            origin="admission",
        ),
    )


@dataclass
class Admission:
    home: Path
    registry: Registry
    scheduler: Scheduler
    # the owner locks this server holds (v0.4 rule 1); its `server_id` is `created.owner`
    ownership: Ownership
    profile: ProfileConfig = ProfileConfig()

    @property
    def server_id(self) -> str:
        return self.ownership.server_id

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
        # v0.4 rule 11: the pool's settings come from config.toml at each admission, so a change
        # applies without a restart (TRESTLE_MAX_RUNNING_RUNS is ignored)
        self.scheduler.max_running = load_config(self.home).max_running_runs
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
            # a lock-free pre-read: a join or a conflict answers before the plan is built, as it
            # always has; the locked step below decides for good
            known = self._key_step(req.idempotency_key, key_call(snap, req, a_hash))
            if not isinstance(known, KeyClaim):
                return known

        # B2-C2: every root is compiled and carved to a plan before any run id exists (a refusal
        # is not a run); a plan-less root gets the implicit depth-1 plan (B2-C1). The whole
        # declared tree is compiled here (MC-23 over MC-34), so a tree defective in any way the
        # declaration and the request show is refused with its own code naming the identifier.
        planned = plan_for_admission(snap, req, deadline_s)
        if isinstance(planned, AdmitResultRefused):
            return planned
        try:
            with admission_lock(self.home):
                return self._admit_locked(req, snap, a_hash, deadline_s, planned)
        except HomeBusy as busy:
            return _home_busy(busy)

    def _admit_locked(
        self,
        req: AdmitRequest,
        snap: PluginSnapshot,
        a_hash: str,
        deadline_s: float,
        planned: AdmittedPlan,
    ) -> AdmitResult:
        """v0.4 rule 7: the admission's locked section, its steps in order. Everything slow
        (registry refresh, import validation, schema validation, compile and carve) ran before the
        lock; process start runs after it."""
        # (1) shared state: home/sched.json (rebuilt from home/live/ when missing), dead servers'
        #     rows dropped; the pool's settings were read from config.toml in `admit`.
        pool = self.scheduler.pool
        state = pool.load() if pool is not None else None
        # (2) the idempotency key: join, else claim (Problem B: home/keys/<sha256(key)>.json,
        #     decided here and written in step 4 (c), before the commit rename). Feature 3 resolves
        #     `after` here too.
        claim = KeyClaim()
        if req.idempotency_key is not None:
            decided = self._key_step(req.idempotency_key, key_call(snap, req, a_hash))
            if not isinstance(decided, KeyClaim):
                return decided
            claim = decided
        # (3) the busy pre-check (rule 6), from home/sched.json: every server's runs.
        busy = self._environment_busy(planned, deadline_s, state)
        if busy is not None:
            return busy
        # (4) admission steps (a) to (d): built hidden, owner-locked, renamed into place.
        admitted = write_admitted_run(
            self.home, snap, req, planned, ownership=self.ownership, retry_of=claim.retry_of
        )
        self.scheduler.mint(admitted.run_id, snap.snapshot_id, admitted.spec_hash)
        # (5) a new waiting run, (7) write home/sched.json. (6) The run's grant comes with its
        #     hand-off to this server's FIFO (`Scheduler.enqueue`), whose pass takes the lock again:
        #     its work order (with the secrets) exists only after admission returns.
        if pool is not None and state is not None:
            pools.add_waiting(
                state,
                self.server_id,
                admitted.run_id,
                admitted.lease_key,
                admitted.arrival,
                admitted.deadline_epoch,
            )
            pool.write(state)
        # the real values travel in memory to the run's WorkOrder and no further
        return AdmitResultAdmitted(tag="admitted", run_id=admitted.run_id, secrets=admitted.secrets)

    def write_run(self, snap: PluginSnapshot, req: AdmitRequest, plan: AdmittedPlan) -> AdmittedRun:
        """`write_admitted_run` for a caller outside `admit` (the harness's `run_tree`, L.SV-5.7):
        under the admission lock, owned by this server."""
        with admission_lock(self.home):
            return write_admitted_run(self.home, snap, req, plan, ownership=self.ownership)

    def _key_step(self, key: str, call: KeyCall) -> AdmitResult | KeyClaim:
        """Problem B's join rule, read from the key's file and never writing it (the unlocked
        pre-read and rule 7 step 2 alike; the claim is written in step 4 (c)). The key joins its
        newest run while that run's `key_expires_at` is in the future and its directory exists:
        an expired key, or one whose run is gone (an admission that died, a run GC removed), is
        free, never a conflict. A join compares plugin, `args_hash`, the call's deadline argument
        and `after`; a mismatch is `admission.idempotency_key_conflict`. One exception frees a
        live key: when the newest run ended interrupted and its plugin was `repeatable` when it
        was admitted, the identical call claims the key for a fresh run (`retry_of`)."""
        entries = keys.read_entries(self.home, key)
        newest = entries[0] if entries else None
        if newest is None or newest.key_expires_at <= keys.now():
            return KeyClaim()
        run_dir = find_run_dir(self.home, newest.run_id)
        if run_dir is None:
            return KeyClaim()
        if newest.same_call(
            plugin=call.plugin,
            args_hash=call.args_hash,
            call_deadline_s=call.call_deadline_s,
            after=call.after,
        ):
            if newest.repeatable and _run_state(run_dir) == "interrupted":
                return KeyClaim(retry_of=newest.run_id)
            return AdmitResultAdmitted(tag="admitted", run_id=newest.run_id, existing=True)
        return AdmitResultRefused(
            tag="refused",
            outcome=RequestOutcome(
                code=codes.IDEMPOTENCY_KEY_CONFLICT,
                message=f"idempotency key conflict: {key}",
                retryable=False,
                origin="admission",
            ),
        )

    def _environment_busy(
        self, plan: AdmittedPlan, deadline_s: float, state: pools.State | None
    ) -> AdmitResultRefused | None:
        """B2 ordering step 2, the lease pre-check (WR-OWN-8, B2-C5): a request whose environment
        is held is queued FIFO within its deadline (`ControlSurface`), unless the latest recorded
        deadline among the key's queued and running runs on every server (`home/sched.json`, held
        runs excluded, v0.3.0 runs included) leaves it less than the plan's worst case plus
        release slice before its own would-be deadline: then it is refused
        `admission.environment_busy` (retryable) with no run id, since waiting could not end in a
        run that fits."""
        if not plan.lease_set or state is None:
            return None
        free_at = pools.busy_until(state, plan.lease_set[0])
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


def key_window_s(deadline_s: float, idempotency_ttl_s: float) -> float:
    """How long after admission an idempotency key stays joinable: `key_expires_at = admitted_at +
    deadline_s + finalization_margin + idempotency_ttl_s`, the run's own deadline (not the 300 s
    snapshot default) plus the margin plus the ttl, so the key outlives its run by the ttl."""
    return deadline_s + clock.finalization_margin + idempotency_ttl_s


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
    arrival: float = 0.0  # created.at, epoch seconds: a key's waiters start in this order
    secrets: dict[str, object] = field(default_factory=dict, repr=False, compare=False)


def _run_state(run_dir: Path) -> str:
    """A run's state for a decision that needs certainty (a join): its state.json, unless that is
    non-terminal while the owner lock is free, then its ledger (v0.4 Problem C)."""
    state = trusted_state(run_dir)
    if state is not None:
        return str(state["state"])
    return RunLedger.open(ledger_path(run_dir)).projected_state()


def _admission_step(step: str) -> None:
    """A test seam: called after each step of `write_admitted_run` (`a`, `b`, `spec`, `created`,
    `key`, `marker`, `d`), so a test can crash an admission between any two of them."""


def write_admitted_run(
    home: Path,
    snap: PluginSnapshot,
    req: AdmitRequest,
    plan: AdmittedPlan,
    *,
    ownership: Ownership,
    retry_of: str | None = None,
) -> AdmittedRun:
    """The post-refusal half of admission (MC-B2-08): mint the run id and build the run, lock
    before visible (v0.4 rule 1). Every refusal has already happened, and the caller holds the
    admission lock (rule 7 step 4; `Admission.write_run` takes it for the harness's `run_tree`,
    L.SV-5.7). The steps:

    (a) `runs/<month>/.adm-<run_id>/evidence/` in one `mkdir(parents=True)`, so the empty-month
        rmdir cannot strand it, and the work directories;
    (b) `flock(LOCK_EX|LOCK_NB)` on `evidence/owner.lock`, held by `ownership` until the run's
        terminal row;
    (c) `spec.json` (with `plan`), the `created` row (owner, has_secrets, the key's recorded
        expiry), the key's claim in `home/keys/` and the live marker `home/live/<run_id>`;
    (d) the rename to `runs/<month>/<run_id>` and an fsync of the month directory: the commit
        point, so a visible run always has a lock holder or a dead owner.

    An admission that dies before (d) leaves debris (an `.adm-` directory, maybe a marker) that
    only the reaper removes, under the admission lock."""
    a_hash = args_hash(req.args)
    deadline_s, _ = deadline_of(snap)
    run_id = generate_run_id()
    run_dir = run_dir_for(home, run_id)
    month_dir = run_dir.parent
    building = month_dir / f"{ADMITTING_PREFIX}{run_id}"
    ev_dir = evidence_dir(building)
    w_dir = work_dir(building)
    ev_dir.mkdir(parents=True)  # (a)
    w_dir.mkdir()
    (w_dir / "tmp").mkdir()
    (w_dir / "outputs").mkdir()
    (w_dir / "artifact-staging").mkdir()
    fsync_dir(building)
    _admission_step("a")

    owner_fd = try_lock(owner_lock_path(building))  # (b)
    if owner_fd is None:  # the directory is this call's own: no one else can hold its lock
        raise RuntimeError(f"owner lock of a new run is held: {run_id}")
    try:
        _admission_step("b")
        deadline = datetime.now(tz=UTC) + timedelta(seconds=deadline_s)
        admitted_at = keys.now()  # the key window's start (the key clock; a test moves it)
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
        atomic_write_json(ev_dir / "spec.json", spec_dict)  # (c)
        # the plan digest is inside the spec, so it is part of the run's identity (design S-8)
        spec_hash = hashlib.sha256(
            json.dumps(spec_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        _admission_step("spec")

        secrets = secret_values(req.args, declared.secrets)
        ledger = RunLedger.open(ledger_path(building))
        created_fields: dict[str, object] = {
            "run_id": run_id,
            "spec_hash": spec_hash,
            # v0.4: the server that admitted the run and holds its owner lock (replaces the
            # service epoch), and whether any declared secret had a value in the call (a reaped
            # run without one has its outputs promoted, rule 3)
            "owner": ownership.server_id,
            "has_secrets": bool(secrets),
            "plugin": snap.plugin,
            "version": snap.version,
            "snapshot_id": snap.snapshot_id,
            "args_hash": a_hash,
            "caller_session": req.caller_session,
            # v0.4 Problem B: the inputs of the key's window, recorded so no later process (a
            # rebuild, another server) recomputes it; the margin is this owner's own (rule 11)
            "deadline_s": deadline_s,
            "finalization_margin_s": clock.finalization_margin,
            "repeatable": declared.repeatable,
        }
        key_entry: keys.KeyEntry | None = None
        if req.idempotency_key is not None:
            call = key_call(snap, req, a_hash)
            ttl_s = load_config(home).idempotency_ttl_s  # Feature 2 seam: the call's own ttl
            # Feature 3 seam: a held run's window starts at its hold_until
            key_expires_at = admitted_at + key_window_s(deadline_s, ttl_s)
            created_fields["idempotency_key"] = req.idempotency_key
            created_fields["idempotency_ttl_s"] = ttl_s
            created_fields["key_expires_at"] = key_expires_at
            created_fields["call_deadline_s"] = call.call_deadline_s
            key_entry = keys.KeyEntry(
                run_id=run_id,
                plugin=snap.plugin,
                args_hash=a_hash,
                key_expires_at=key_expires_at,
                deadline_s=deadline_s,
                call_deadline_s=call.call_deadline_s,
                after=call.after,
                repeatable=declared.repeatable,
                retry_of=retry_of,
            )
        if retry_of is not None:
            created_fields["retry_of"] = retry_of
        # WR-OWN-8: the environment key the run holds a lease on, when it holds one; absent
        # otherwise, so a run that declares no environment writes the same row as before
        lease_key = plan.lease_set[0] if plan.lease_set else None
        if lease_key is not None:
            created_fields[lease.LEASE_KEY_FIELD] = lease_key
        created = ledger.append("created", **created_fields)
        _admission_step("created")

        if req.idempotency_key is not None and key_entry is not None:
            keys.claim(home, req.idempotency_key, key_entry)
        _admission_step("key")

        write_marker(
            home,
            run_id,
            {
                "owner": ownership.server_id,
                "month": month_dir.name,
                "state": "queued",
                "lease_key": lease_key,
                "deadline": deadline.timestamp(),
                "arrival": created["at"],
            },
        )
        _admission_step("marker")

        os.rename(building, run_dir)  # (d) the commit point
        _admission_step("d")
        fsync_dir(month_dir)
    except BaseException:
        os.close(owner_fd)  # what the process's death would do: the reaper owns the debris
        raise
    ownership.adopt(run_id, owner_fd)

    return AdmittedRun(
        run_id=run_id,
        spec_hash=spec_hash,
        lease_key=lease_key,
        deadline_epoch=deadline.timestamp(),
        run_dir=run_dir,
        arrival=pools.epoch(created["at"]),
        secrets=secrets,
    )
