"""Artifact and milestone helpers for pack plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class PackContext(Protocol):
    def log(self, message: str) -> None: ...
    def progress(self, message: str, *, fraction: float | None = None) -> None: ...
    def artifact(self, name: str) -> Path: ...
    def attach(self, path: Path, *, name: str) -> str: ...


@dataclass
class PackArtifacts:
    """Collect artifact ids and stage markers during a pack run."""

    stage: str = ""
    artifact_ids: dict[str, str] = field(default_factory=dict)

    def milestone(self, ctx: PackContext, message: str, *, fraction: float | None = None) -> None:
        prefix = f"[{self.stage}] " if self.stage else ""
        ctx.log(f"{prefix}{message}")
        if fraction is not None:
            ctx.progress(f"{prefix}{message}", fraction=fraction)

    def attach_file(self, ctx: PackContext, path: Path, *, name: str) -> str:
        art_id = ctx.attach(path, name=name)
        self.artifact_ids[name] = art_id
        return art_id

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "artifacts": dict(self.artifact_ids)}
