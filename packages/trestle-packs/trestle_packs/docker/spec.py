"""Docker stack declarative spec."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class WaitMode(StrEnum):
    STARTED = "started"
    HEALTHY = "healthy"
    CUSTOM = "custom"


@dataclass
class ProbeSpec:
    kind: str = "command"
    command: list[str] = field(default_factory=list)


@dataclass
class WaveSpec:
    name: str
    services: list[str]
    wait: WaitMode = WaitMode.HEALTHY
    timeout_s: float = 120.0


@dataclass
class StackSpec:
    project: str | None = None
    compose_file: str = "docker-compose.yml"
    teardown: str = "down"
    waves: list[WaveSpec] = field(default_factory=list)
    probes: dict[str, ProbeSpec] = field(default_factory=dict)
    depends_on: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StackSpec:
        waves = [
            WaveSpec(
                name=str(w.get("name", f"wave-{idx}")),
                services=[str(s) for s in w.get("services", [])],
                wait=WaitMode(str(w.get("wait", WaitMode.HEALTHY.value))),
                timeout_s=float(w.get("timeout_s", 120.0)),
            )
            for idx, w in enumerate(raw.get("waves", []))
        ]
        probes = {
            name: ProbeSpec(
                kind=str(spec.get("kind", "command")),
                command=[str(c) for c in spec.get("command", [])],
            )
            for name, spec in raw.get("probes", {}).items()
        }
        depends_raw = raw.get("depends_on", {})
        depends_on = {str(k): [str(v) for v in vals] for k, vals in depends_raw.items()}
        return cls(
            project=raw.get("project"),
            compose_file=str(raw.get("compose_file", "docker-compose.yml")),
            teardown=str(raw.get("teardown", "down")),
            waves=waves,
            probes=probes,
            depends_on=depends_on,
        )

    def compose_path(self, *, cwd: Path | None = None) -> Path:
        path = Path(self.compose_file)
        if cwd is not None and not path.is_absolute():
            return (cwd / path).resolve()
        return path.resolve()

    def all_services(self) -> list[str]:
        names: list[str] = []
        for wave in self.waves:
            for name in wave.services:
                if name not in names:
                    names.append(name)
        return names
