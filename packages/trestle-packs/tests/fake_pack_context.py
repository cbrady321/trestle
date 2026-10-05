"""Minimal PackContext for pack library tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FakePackContext:
    work: Path
    logs: list[str] = field(default_factory=list)
    attached: dict[str, Path] = field(default_factory=dict)
    _attach_counter: int = 0

    def log(self, message: str) -> None:
        self.logs.append(message)

    def progress(self, message: str, *, fraction: float | None = None) -> None:
        self.logs.append(message)

    def artifact(self, name: str) -> Path:
        path = self.work / "artifact-staging" / f"{name}.partial"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def attach(self, path: Path, *, name: str) -> str:
        if not path.is_relative_to(self.work):
            msg = "attach path must be under work/"
            raise ValueError(msg)
        self._attach_counter += 1
        art_id = f"art_{self._attach_counter}"
        self.attached[name] = path
        return art_id
