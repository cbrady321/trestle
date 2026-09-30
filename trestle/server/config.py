"""Trestle service configuration (R-OPS-5)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

_GB = 1024**3

# Run capacity (MC-30, OQ-10): `max_running_runs` runs hold a slot at once and up to `queue_depth`
# more wait in a FIFO; a run beyond both is refused `admission.queue_full`. The two defaults are
# provisional: OQ-10 sets them from the Slice A workload measurement (TM-C3).
CAPACITY_DEFAULT_PROVISIONAL = True
MAX_RUNNING_RUNS_DEFAULT = 8
QUEUE_DEPTH_DEFAULT = 256


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
class TrestleConfig:
    retention: RetentionConfig = RetentionConfig()
    idempotency_ttl_s: int = 3600
    service_log: Path | None = None
    max_running_runs: int = MAX_RUNNING_RUNS_DEFAULT
    queue_depth: int = QUEUE_DEPTH_DEFAULT
    profile: ProfileConfig = ProfileConfig()

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
            max_running_runs=max(1, _env_int("TRESTLE_MAX_RUNNING_RUNS", self.max_running_runs)),
            queue_depth=max(0, _env_int("TRESTLE_QUEUE_DEPTH", self.queue_depth)),
            profile=self.profile,
        )


def load_config(home: Path) -> TrestleConfig:
    """Load config.toml when present; otherwise use documented defaults."""
    cfg = TrestleConfig.defaults()
    path = home / "config.toml"
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
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
    if cfg.service_log is None:
        cfg = replace(cfg, service_log=home / "service.log")
    return cfg.with_env_overrides()


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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


def _optional_path(raw: object) -> Path | None:
    if raw is None:
        return None
    return Path(str(raw))
