"""Docker compose backend protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from trestle_packs.docker.spec import StackSpec, WaitMode


class ComposeBackend(Protocol):
    def up(
        self,
        spec: StackSpec,
        services: list[str],
        *,
        wait: WaitMode,
        timeout_s: float,
        cwd: Path,
    ) -> None: ...

    def down(self, spec: StackSpec, *, cwd: Path, remove_volumes: bool = False) -> None: ...

    def stop(self, spec: StackSpec, *, cwd: Path) -> None: ...

    def service_logs(
        self,
        spec: StackSpec,
        services: list[str],
        *,
        cwd: Path,
    ) -> dict[str, str]: ...

    def is_available(self) -> bool: ...
