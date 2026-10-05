"""Lane-local fake `ComposeBackend` for G-E3 (never an engine). Records
every call; `fail_up` makes `up` raise so a stack failure can be driven."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FakeComposeBackend:
    def __init__(self, *, fail_up: bool = False) -> None:
        self.fail_up = fail_up
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def up(self, spec: Any, services: list[str], **kwargs: Any) -> None:
        self.calls.append("up")
        if self.fail_up:
            msg = "fake backend: up failed"
            raise RuntimeError(msg)

    def down(self, spec: Any, *, cwd: Path, remove_volumes: bool = False) -> None:
        self.calls.append("down")

    def stop(self, spec: Any, *, cwd: Path) -> None:
        self.calls.append("stop")

    def service_logs(self, spec: Any, services: list[str], *, cwd: Path) -> dict[str, str]:
        return {}
