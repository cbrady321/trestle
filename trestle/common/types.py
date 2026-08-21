"""Shared domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Handle = str
JoinMode = Literal["all", "any", "first_failure"]


@dataclass
class RequestOutcome:
    code: str
    message: str
    retryable: bool
    origin: Literal["admission", "projection"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "origin": self.origin,
        }


@dataclass
class AdmitRequest:
    plugin: str
    args: dict[str, Any]
    version: str | None = None
    idempotency_key: str | None = None


@dataclass
class AdmitResultRefused:
    tag: Literal["refused"]
    outcome: RequestOutcome


@dataclass
class AdmitResultAdmitted:
    tag: Literal["admitted"]
    run_id: Handle
    existing: bool = False


AdmitResult = AdmitResultRefused | AdmitResultAdmitted


@dataclass
class WorkOrder:
    run_id: Handle
    snapshot_id: str
    spec_hash: str


@dataclass
class RunSpec:
    plugin: str
    version: str
    snapshot_id: str
    args: dict[str, Any]
    args_hash: str
    source_sha256: str
    schema_sha256: str
    manifest_sha256: str
    python_version: str
    platform: str
    summary_budget: int
    timeout_s: int
    resolved_artifacts: dict[str, str] = field(default_factory=dict)
    deadline: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "plugin": self.plugin,
            "version": self.version,
            "snapshot_id": self.snapshot_id,
            "args": self.args,
            "args_hash": self.args_hash,
            "source_sha256": self.source_sha256,
            "schema_sha256": self.schema_sha256,
            "manifest_sha256": self.manifest_sha256,
            "python_version": self.python_version,
            "platform": self.platform,
            "summary_budget": self.summary_budget,
            "timeout_s": self.timeout_s,
            "resolved_artifacts": self.resolved_artifacts,
        }
        if self.deadline is not None:
            out["deadline"] = self.deadline
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunSpec:
        return cls(
            plugin=str(data["plugin"]),
            version=str(data["version"]),
            snapshot_id=str(data["snapshot_id"]),
            args=dict(data["args"]),
            args_hash=str(data["args_hash"]),
            source_sha256=str(data["source_sha256"]),
            schema_sha256=str(data["schema_sha256"]),
            manifest_sha256=str(data["manifest_sha256"]),
            python_version=str(data["python_version"]),
            platform=str(data["platform"]),
            summary_budget=int(data["summary_budget"]),
            timeout_s=int(data["timeout_s"]),
            resolved_artifacts=dict(data.get("resolved_artifacts", {})),
            deadline=data.get("deadline"),
        )


@dataclass
class PluginSnapshot:
    snapshot_id: str
    plugin: str
    version: str
    source_path: str
    source_sha256: str
    schema_sha256: str
    manifest_sha256: str
    summary_budget: int
    timeout_s: int


@dataclass
class RunView:
    run_id: Handle
    state: str
    status_frame_version: int = 1
    duration_ms: int | None = None
    event_count: int = 0
    artifact_count: int = 0
    result_bytes: int | None = None
    truncated: bool = False
    omitted: list[str] | None = None
    summary: Any = None
    error: dict[str, Any] | None = None
    next: Handle | None = None
    limits_exceeded: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "state": self.state,
            "status_frame_version": self.status_frame_version,
            "truncated": self.truncated,
        }
        if self.duration_ms is not None:
            out["duration_ms"] = self.duration_ms
        out["event_count"] = self.event_count
        out["artifact_count"] = self.artifact_count
        if self.result_bytes is not None:
            out["result_bytes"] = self.result_bytes
        if self.omitted is not None:
            out["omitted"] = self.omitted
        if self.summary is not None:
            out["summary"] = self.summary
        if self.error is not None:
            out["error"] = self.error
        if self.next is not None:
            out["next"] = self.next
        if self.limits_exceeded is not None:
            out["limits_exceeded"] = self.limits_exceeded
        return out


StatusFrame = RunView


@dataclass
class PluginCatalogRow:
    name: str
    version: str
    description: str
    valid: bool
    capability_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "valid": self.valid,
            "capability_class": self.capability_class,
        }


@dataclass
class CatalogView:
    registry_version: int
    items: list[PluginCatalogRow]
    next_cursor: Handle | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "registry_version": self.registry_version,
            "items": [row.to_dict() for row in self.items],
            "truncated": self.truncated,
        }
        if self.next_cursor is not None:
            out["next_cursor"] = self.next_cursor
        return out
