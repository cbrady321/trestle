"""In-memory ComposeBackend for StackRunner integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from trestle_packs.docker.spec import StackSpec, WaitMode


@dataclass
class UpCall:
    services: list[str]
    wait: WaitMode
    timeout_s: float
    cwd: Path


@dataclass
class FakeComposeBackend:
    """Records compose operations and can fail on a chosen wave index."""

    fail_on_wave: int | None = None
    up_calls: list[UpCall] = field(default_factory=list)
    down_calls: list[bool] = field(default_factory=list)
    stop_calls: int = 0
    _wave_index: int = 0
    _log_lines: dict[str, list[str]] = field(default_factory=dict)

    def is_available(self) -> bool:
        return True

    def up(
        self,
        spec: StackSpec,
        services: list[str],
        *,
        wait: WaitMode,
        timeout_s: float,
        cwd: Path,
    ) -> None:
        if self.fail_on_wave is not None and self._wave_index == self.fail_on_wave:
            msg = f"simulated failure on wave {self._wave_index}"
            raise RuntimeError(msg)
        self.up_calls.append(
            UpCall(services=list(services), wait=wait, timeout_s=timeout_s, cwd=cwd)
        )
        for service in services:
            self._log_lines.setdefault(service, []).append(
                f"[wave {self._wave_index}] {service} started (wait={wait.value})"
            )
        self._wave_index += 1

    def down(self, spec: StackSpec, *, cwd: Path, remove_volumes: bool = False) -> None:
        self.down_calls.append(remove_volumes)

    def stop(self, spec: StackSpec, *, cwd: Path) -> None:
        self.stop_calls += 1

    def service_logs(
        self,
        spec: StackSpec,
        services: list[str],
        *,
        cwd: Path,
    ) -> dict[str, str]:
        return {service: "\n".join(self._log_lines.get(service, [])) + "\n" for service in services}
