"""Trestle service configuration (R-OPS-5)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

_GB = 1024**3


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
            )
    if cfg.service_log is None:
        cfg = TrestleConfig(
            retention=cfg.retention,
            idempotency_ttl_s=cfg.idempotency_ttl_s,
            service_log=home / "service.log",
        )
    return cfg.with_env_overrides()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


def _optional_path(raw: object) -> Path | None:
    if raw is None:
        return None
    return Path(str(raw))
