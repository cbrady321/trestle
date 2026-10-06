"""Trestle service configuration (R-OPS-5)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from trestle.common import clock

_GB = 1024**3

# Run capacity (MC-30, OQ-10): `max_running_runs` runs hold a slot at once and up to `queue_depth`
# more wait in a FIFO; a run beyond both is refused `admission.queue_full`. `max_running_runs` is
# the measured one: the largest number of concurrent runs of the Slice A workload that ended every
# run within its deadline, left no process behind and kept the p95 run time within the
# measurement's degradation bound, on the recorded machine (`tests/tree/capacity_slice_a.json`,
# `python -m tests.tree.capacity measure`, L.TR-6.8). `queue_depth` was not measured: a queued run
# is bounded by its own deadline, not by the depth.
MAX_RUNNING_RUNS_DEFAULT = 31
QUEUE_DEPTH_DEFAULT = 256
# v0.4: `max_running_runs` is one pool per home, shared by every server on it (`[operator]
# max_share`, unset by default, caps one server's slots); `queue_depth` and `[operator]
# max_held_runs` (Feature 3's held runs) bound each server's own FIFO, read at start.
MAX_HELD_RUNS_DEFAULT = 256
# v0.4 Feature 0: `[operator] deadline_ceiling_s` bounds a call's `deadline_s` (default: the
# clock's 3,600 s, so nothing changes until an operator raises it); a value above the hard cap
# stops the load.
DEADLINE_CEILING_MAX_S = 86400
# v0.4 Feature 2: `[keys] max_ttl_s`, the longest `idempotency_ttl_s` a call may ask for (7 days;
# held to `metadata_days` when that is shorter, and an explicit value above it stops the load)
MAX_TTL_S_DEFAULT = 604800
_DAY_S = 86400
# v0.4: the environment override of the pool size, now ignored (doctor warns when it is set)
IGNORED_MAX_RUNNING_ENV = "TRESTLE_MAX_RUNNING_RUNS"


# The service profile (MC-CORE-07, WR-AUTH-1/2): `full` registers all ten tools and admits any
# published plugin; `restricted` drops `publish_plugin`, admits only allowlisted plugins and lets a
# session cancel only the runs it started. Read once at start, from the operator's config.toml
# only: no tool argument, plugin intent or environment value can change it.
PROFILE_FULL = "full"
PROFILE_RESTRICTED = "restricted"
PROFILE_MODES = (PROFILE_FULL, PROFILE_RESTRICTED)


@dataclass(frozen=True)
class ProfileConfig:
    mode: str = PROFILE_FULL
    allowlist: tuple[str, ...] = ()

    @property
    def restricted(self) -> bool:
        return self.mode == PROFILE_RESTRICTED

    def allows(self, plugin: str) -> bool:
        return not self.restricted or plugin in self.allowlist


@dataclass(frozen=True)
class RetentionConfig:
    metadata_days: int = 180
    artifact_days: int = 7
    abandoned_hours: int = 24
    storage_cap_bytes: int = 10 * _GB


@dataclass(frozen=True)
class OperatorLimits:
    """B2's `OperatorLimits` (MC-B2-04): the bounds the host enforces and the sweep reads. Every
    timing field defaults to `trestle/common/clock.py`'s one definition of it (MC-09, SA-05), read
    when the config is built, so no timing literal lives here. `release_executables` is the
    absolute paths the sweep may run for an `ArgvRelease` (V-10.1): empty by default, since its
    owner and default are still open (design section 10 F-11(b), TM-B2-2); an operator lists them
    in `config.toml` `[operator] release_executables` (decision 2026-09-30, L.RB-0.3.fix)."""

    deadline_ceiling: float = field(default_factory=lambda: clock.deadline_ceiling)
    release_slice: float = field(default_factory=lambda: clock.release_slice)
    grace: float = field(default_factory=lambda: clock.grace)
    kill: float = field(default_factory=lambda: clock.kill)
    finalization_margin: float = field(default_factory=lambda: clock.finalization_margin)
    sweep_parallelism: int = field(default_factory=lambda: clock.sweep_parallelism)
    release_executables: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TrestleConfig:
    retention: RetentionConfig = RetentionConfig()
    idempotency_ttl_s: int = 3600
    service_log: Path | None = None
    max_running_runs: int = MAX_RUNNING_RUNS_DEFAULT
    queue_depth: int = QUEUE_DEPTH_DEFAULT
    profile: ProfileConfig = ProfileConfig()
    operator_limits: OperatorLimits = field(default_factory=OperatorLimits)
    max_share: int | None = None
    max_held_runs: int = MAX_HELD_RUNS_DEFAULT
    max_ttl_s: int = MAX_TTL_S_DEFAULT

    @classmethod
    def defaults(cls) -> TrestleConfig:
        return cls(
            retention=RetentionConfig(),
            idempotency_ttl_s=3600,
            service_log=None,
        )

    def with_env_overrides(self) -> TrestleConfig:
        retention = self.retention
        metadata_days = _env_int("TRESTLE_METADATA_DAYS", retention.metadata_days)
        artifact_days = _env_int("TRESTLE_ARTIFACT_DAYS", retention.artifact_days)
        abandoned_hours = _env_int("TRESTLE_ABANDONED_HOURS", retention.abandoned_hours)
        cap_gb = os.environ.get("TRESTLE_STORAGE_CAP_GB")
        storage_cap_bytes = retention.storage_cap_bytes
        if cap_gb is not None:
            storage_cap_bytes = int(float(cap_gb) * _GB)
        ttl = _env_int("TRESTLE_IDEMPOTENCY_TTL_S", self.idempotency_ttl_s)
        return TrestleConfig(
            retention=RetentionConfig(
                metadata_days=metadata_days,
                artifact_days=artifact_days,
                abandoned_hours=abandoned_hours,
                storage_cap_bytes=storage_cap_bytes,
            ),
            idempotency_ttl_s=ttl,
            service_log=self.service_log,
            # v0.4 rule 11: the pool size every server must agree on comes from config.toml only;
            # TRESTLE_MAX_RUNNING_RUNS is ignored (doctor warns when it is set)
            max_running_runs=max(1, self.max_running_runs),
            queue_depth=max(0, _env_int("TRESTLE_QUEUE_DEPTH", self.queue_depth)),
            profile=self.profile,
            operator_limits=self.operator_limits,
            max_share=self.max_share,
            max_held_runs=self.max_held_runs,
            max_ttl_s=self.max_ttl_s,
        )


def load_config(home: Path) -> TrestleConfig:
    """Load config.toml when present; otherwise use documented defaults."""
    cfg = TrestleConfig.defaults()
    explicit_max_ttl: int | None = None
    path = home / "config.toml"
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        explicit_max_ttl = _load_max_ttl(raw.get("keys"))
        retention_raw = raw.get("retention", {})
        if isinstance(retention_raw, dict):
            metadata_days = int(retention_raw.get("metadata_days", cfg.retention.metadata_days))
            artifact_days = int(retention_raw.get("artifact_days", cfg.retention.artifact_days))
            abandoned_hours = int(
                retention_raw.get("abandoned_hours", cfg.retention.abandoned_hours)
            )
            if "storage_cap_gb" in retention_raw:
                storage_cap_bytes = int(float(retention_raw["storage_cap_gb"]) * _GB)
            elif "storage_cap_bytes" in retention_raw:
                storage_cap_bytes = int(retention_raw["storage_cap_bytes"])
            else:
                storage_cap_bytes = cfg.retention.storage_cap_bytes
            cfg = TrestleConfig(
                retention=RetentionConfig(
                    metadata_days=metadata_days,
                    artifact_days=artifact_days,
                    abandoned_hours=abandoned_hours,
                    storage_cap_bytes=storage_cap_bytes,
                ),
                idempotency_ttl_s=int(raw.get("idempotency_ttl_s", cfg.idempotency_ttl_s)),
                service_log=_optional_path(raw.get("service_log")),
                max_running_runs=int(raw.get("max_running_runs", cfg.max_running_runs)),
                queue_depth=int(raw.get("queue_depth", cfg.queue_depth)),
            )
        cfg = replace(cfg, profile=_load_profile(raw.get("profile")))
        cfg = _load_pool_operator(cfg, raw.get("operator"))
        cfg = _load_deadline_ceiling(cfg, raw.get("operator"))
        executables = _load_release_executables(raw.get("operator"))
        if executables:
            limits = replace(cfg.operator_limits, release_executables=executables)
            cfg = replace(cfg, operator_limits=limits)
    if cfg.service_log is None:
        cfg = replace(cfg, service_log=home / "service.log")
    return _with_ttl_maximum(cfg.with_env_overrides(), explicit_max_ttl)


def _load_profile(raw: object) -> ProfileConfig:
    """`[profile] mode = "full" | "restricted"`, `allowlist = [plugin names]`. An unknown mode or a
    malformed allowlist stops the load: a config that means to restrict must never run as full."""
    if raw is None:
        return ProfileConfig()
    if not isinstance(raw, dict):
        raise ValueError("config.toml [profile] must be a table")
    mode = raw.get("mode", PROFILE_FULL)
    if mode not in PROFILE_MODES:
        raise ValueError(f"config.toml [profile] mode must be one of {PROFILE_MODES}, got {mode!r}")
    allowlist = raw.get("allowlist", [])
    if not isinstance(allowlist, list) or not all(isinstance(item, str) for item in allowlist):
        raise ValueError("config.toml [profile] allowlist must be a list of plugin names")
    return ProfileConfig(mode=mode, allowlist=tuple(allowlist))


def _load_pool_operator(cfg: TrestleConfig, raw: object) -> TrestleConfig:
    """`[operator] max_share` (a cap on one server's slots of the home's pool, at least 1; unset:
    no cap) and `max_held_runs` (each server's bound on held runs, at least 0)."""
    if not isinstance(raw, dict):
        return cfg
    if "max_share" in raw:
        share = raw["max_share"]
        if not isinstance(share, int) or isinstance(share, bool) or share < 1:
            raise ValueError("config.toml [operator] max_share must be an integer of at least 1")
        cfg = replace(cfg, max_share=share)
    if "max_held_runs" in raw:
        held = raw["max_held_runs"]
        if not isinstance(held, int) or isinstance(held, bool) or held < 0:
            raise ValueError("config.toml [operator] max_held_runs must be an integer >= 0")
        cfg = replace(cfg, max_held_runs=held)
    return cfg


def _load_max_ttl(raw: object) -> int | None:
    """`[keys] max_ttl_s` (seconds, at least 0): the longest `idempotency_ttl_s` a call may ask
    for (Feature 2). None when unset; a malformed value stops the load."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("config.toml [keys] must be a table")
    if "max_ttl_s" not in raw:
        return None
    ttl = raw["max_ttl_s"]
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 0:
        raise ValueError("config.toml [keys] max_ttl_s must be an integer >= 0")
    return ttl


def _with_ttl_maximum(cfg: TrestleConfig, explicit: int | None) -> TrestleConfig:
    """Settle `max_ttl_s` against the final retention: a key must not outlive the metadata that
    answers it, so an explicit maximum above `metadata_days` stops the load, and an unset one is
    held to `metadata_days` when that is shorter than the 7-day default."""
    limit = cfg.retention.metadata_days * _DAY_S
    if explicit is None:
        return replace(cfg, max_ttl_s=min(MAX_TTL_S_DEFAULT, limit))
    if explicit > limit:
        raise ValueError(
            f"config.toml [keys] max_ttl_s {explicit} is above metadata_days "
            f"({cfg.retention.metadata_days} days = {limit} s)"
        )
    return replace(cfg, max_ttl_s=explicit)


def _load_deadline_ceiling(cfg: TrestleConfig, raw: object) -> TrestleConfig:
    """`[operator] deadline_ceiling_s` (seconds, above 0 and at most 86,400): the longest deadline
    a call may ask for (Feature 0). Unset leaves the default; a bad value stops the load."""
    if not isinstance(raw, dict) or "deadline_ceiling_s" not in raw:
        return cfg
    ceiling = raw["deadline_ceiling_s"]
    if (
        not isinstance(ceiling, int | float)
        or isinstance(ceiling, bool)
        or not 0 < ceiling <= DEADLINE_CEILING_MAX_S
    ):
        raise ValueError(
            f"config.toml [operator] deadline_ceiling_s must be above 0 and at most "
            f"{DEADLINE_CEILING_MAX_S}"
        )
    return replace(
        cfg, operator_limits=replace(cfg.operator_limits, deadline_ceiling=float(ceiling))
    )


def _load_release_executables(raw: object) -> frozenset[str]:
    """`[operator] release_executables = [absolute paths]`: the executables the host sweep may run
    for an `ArgvRelease` (V-10.1, B2-C9). Unset (no table, no key) leaves the library default,
    EMPTY (F-11(b), TM-B2-2); a malformed list or a relative path stops the load, never widens
    it."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, dict):
        raise ValueError("config.toml [operator] must be a table")
    listed = raw.get("release_executables", [])
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise ValueError("config.toml [operator] release_executables must be a list of paths")
    for item in listed:
        if not os.path.isabs(item):
            raise ValueError(f"config.toml [operator] release_executables: {item!r} not absolute")
    return frozenset(listed)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


def _optional_path(raw: object) -> Path | None:
    if raw is None:
        return None
    return Path(str(raw))
