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
    origin: Literal["admission", "projection", "publication"]

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
    # The MCP session the call arrived on (None outside an MCP session); written on the `created`
    # row so the restricted profile can scope `cancel` to the session that started a run.
    caller_session: str | None = None


@dataclass
class AdmitResultRefused:
    tag: Literal["refused"]
    outcome: RequestOutcome


@dataclass
class AdmitResultAdmitted:
    tag: Literal["admitted"]
    run_id: Handle
    existing: bool = False
    # The real values of the run's declared secret arguments, by declared name (MC-CORE-13). Held
    # in memory from admission to the run's start and never written: not in `spec.json`, the
    # ledger or the idempotency store; hidden from repr and comparison.
    secrets: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


AdmitResult = AdmitResultRefused | AdmitResultAdmitted


@dataclass
class WorkOrder:
    run_id: Handle
    snapshot_id: str
    spec_hash: str
    # In-memory only (see `AdmitResultAdmitted.secrets`): delivered to the wrapper and child
    # through the environment, never persisted; hidden from repr and comparison.
    secrets: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


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
    provenance: dict[str, Any] = field(default_factory=lambda: {"packages": {}})
    # The admitted plan (MC-20, L.SV-3.4): B2's `PlanAccepted` plus format, vertices and edges, as
    # `AdmittedPlan.to_json` encodes it. None for a spec written before plans (read as the
    # implicit depth-1 plan, B2-C1).
    plan: dict[str, Any] | None = None

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
            "provenance": self.provenance,
        }
        if self.deadline is not None:
            out["deadline"] = self.deadline
        if self.plan is not None:
            out["plan"] = self.plan
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
            provenance=_provenance(data.get("provenance")),
            plan=dict(data["plan"]) if isinstance(data.get("plan"), dict) else None,
        )


def _provenance(raw: object) -> dict[str, Any]:
    """`spec.json`'s `provenance`: the declared packages and the digests recorded for them at
    publication (MC-18). A spec that predates it records none."""
    packages = raw.get("packages") if isinstance(raw, dict) else None
    return {
        "packages": {str(k): str(v) for k, v in packages.items()}
        if isinstance(packages, dict)
        else {}
    }


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


@dataclass(frozen=True)
class DeclaredMetadata:
    """What a plugin declares statically in its decorator call form, plus its entry name.

    Carried in `manifest.json` as `declared` and `entry` (MC-18) and read only through
    `trestle.server.snapshots.load_declared`. An undeclared plugin carries the defaults; the
    300 s default deadline is applied by admission, so `deadline_s` stays `None` here.
    """

    entry: str | None = None
    deadline_s: float | None = None
    summary_fields: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    env_arg: str | None = None
    secrets: frozenset[str] = frozenset()
    # Recorded at publication, not declared in source: each declared package's digest as the
    # publication validator resolved it. Empty for a plugin that declares no packages.
    package_digests: dict[str, str] = field(default_factory=dict)

    def declared_dict(self) -> dict[str, Any]:
        """The manifest's `declared` object: canonical, JSON-safe, sorted where unordered."""
        return {
            "deadline_s": self.deadline_s,
            "summary_fields": list(self.summary_fields),
            "packages": list(self.packages),
            "env_arg": self.env_arg,
            "secrets": sorted(self.secrets),
            "package_digests": {k: self.package_digests[k] for k in sorted(self.package_digests)},
        }

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> DeclaredMetadata:
        """Read `declared` and `entry` from a manifest dict; a manifest that predates them
        (no `declared` key) yields the defaults."""
        entry = manifest.get("entry")
        raw = manifest.get("declared")
        declared: dict[str, Any] = raw if isinstance(raw, dict) else {}
        deadline = declared.get("deadline_s")
        env_arg = declared.get("env_arg")
        return cls(
            entry=str(entry) if isinstance(entry, str) else None,
            deadline_s=deadline
            if isinstance(deadline, (int, float)) and not isinstance(deadline, bool)
            else None,
            summary_fields=tuple(str(x) for x in declared.get("summary_fields", ())),
            packages=tuple(str(x) for x in declared.get("packages", ())),
            env_arg=str(env_arg) if isinstance(env_arg, str) else None,
            secrets=frozenset(str(x) for x in declared.get("secrets", ())),
            package_digests={
                str(k): str(v)
                for k, v in (
                    declared["package_digests"]
                    if isinstance(declared.get("package_digests"), dict)
                    else {}
                ).items()
            },
        )


@dataclass
class CleanupView:
    """The cleanup disposition of a finished run's process-group target (B2-C9, B4-C7): for a
    spawned run `released` only when the supervisor confirmed every process attributable to the run
    gone, else `unknown`; never `nothing_created` (the run spawned a process), never clean by
    default. A run finalized while queued never spawned one and reads `nothing_created` (B2-C12)."""

    processes: str

    def to_dict(self) -> dict[str, Any]:
        return {"processes": self.processes}


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
    cleanup: CleanupView | None = None
    outcome: dict[str, Any] | None = None  # MC-17: {class, code, identity, recovered}, beside state
    # B4-C6: the TerminalAnswer, one additive key beside today's run state (present only once the
    # terminal row exists, so a non-terminal frame never carries a class, WR-TERM-2)
    answer: dict[str, Any] | None = None
    # MC-B3-08: a child view only (V-1.3): the root it belongs to, the vertex's canonical path and
    # its B4 `Listing` when that is `not_started`, `stopped` or `unended` (else None). A root view
    # leaves all three unset and emits none of them, so its bytes are unchanged.
    root_run_id: str | None = None
    path: str | None = None
    disposition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "state": self.state,
            "status_frame_version": self.status_frame_version,
            "truncated": self.truncated,
        }
        if self.root_run_id is not None:
            out["root_run_id"] = self.root_run_id
            out["path"] = self.path
            out["disposition"] = self.disposition
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
        if self.cleanup is not None:
            out["cleanup"] = self.cleanup.to_dict()
        if self.outcome is not None:
            out["outcome"] = self.outcome
        if self.answer is not None:
            out["answer"] = self.answer
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
class PublishView:
    name: str
    snapshot_id: str
    registry_version: int
    source_sha256: str
    created: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "snapshot_id": self.snapshot_id,
            "registry_version": self.registry_version,
            "source_sha256": self.source_sha256,
            "created": self.created,
        }


@dataclass
class CatalogView:
    registry_version: int
    items: list[PluginCatalogRow]
    plugin_search_paths: list[str]
    next_cursor: Handle | None = None
    truncated: bool = False
    catalog_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "registry_version": self.registry_version,
            "items": [row.to_dict() for row in self.items],
            "truncated": self.truncated,
            "plugin_search_paths": list(self.plugin_search_paths),
        }
        if self.catalog_hint is not None:
            out["catalog_hint"] = self.catalog_hint
        if self.next_cursor is not None:
            out["next_cursor"] = self.next_cursor
        return out
