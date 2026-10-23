"""What `docker_stack` answers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StackResult:
    """One `docker_stack` call's verdict.

    `ok` is the verdict. On a failure `failed_step` names the step (`config`, a wave's name,
    `stop`, `down`) and `error` quotes the end of docker's output; `teardown` is what ran after a
    failed `up` (`down`, `stop` or `none`). `logs` maps each service to the artifact handle of its
    `docker compose logs` output."""

    ok: bool
    action: str
    project: str | None
    services: list[str] = field(default_factory=list)
    waves_completed: list[str] = field(default_factory=list)
    failed_step: str | None = None
    error: str | None = None
    teardown: str | None = None
    logs: dict[str, str] = field(default_factory=dict)
