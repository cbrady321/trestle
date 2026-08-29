"""Docker stack orchestration runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trestle_packs.core.artifacts import PackArtifacts
from trestle_packs.core.dag import plan_waves
from trestle_packs.core.teardown import TeardownPolicy
from trestle_packs.docker.backend import ComposeBackend
from trestle_packs.docker.compose_whale import WhaleComposeBackend
from trestle_packs.docker.spec import StackSpec, WaitMode


@dataclass
class StackResult:
    project: str | None
    services: list[str]
    waves_completed: list[str] = field(default_factory=list)
    artifacts: PackArtifacts = field(default_factory=PackArtifacts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "services": self.services,
            "waves_completed": self.waves_completed,
            **self.artifacts.to_dict(),
        }


class StackRunner:
    def __init__(self, ctx: Any, *, backend: ComposeBackend | None = None) -> None:
        self._ctx = ctx
        self._backend = backend or WhaleComposeBackend()
        self._artifacts = PackArtifacts(stage="docker")

    def up(self, spec: StackSpec, *, cwd: Path | None = None) -> StackResult:
        if not self._backend.is_available():
            msg = "docker CLI not found — install Docker Desktop or docker engine"
            raise RuntimeError(msg)

        workdir = cwd or Path.cwd()
        compose_file = spec.compose_path(cwd=workdir)
        if not compose_file.exists():
            msg = f"compose file not found: {compose_file}"
            raise FileNotFoundError(msg)

        services = spec.all_services()
        if not services:
            msg = "stack spec has no services"
            raise ValueError(msg)

        explicit = [wave.services for wave in spec.waves] if spec.waves else None
        plan = plan_waves(
            services,
            depends_on=spec.depends_on,
            explicit_waves=explicit,
        )

        result = StackResult(project=spec.project, services=list(plan.flat()))
        policy = TeardownPolicy(spec.teardown)

        try:
            total = len(plan.waves)
            for idx, wave in enumerate(plan.waves):
                wave_spec = _wave_for_services(spec, wave)
                wait = wave_spec.wait if wave_spec else WaitMode.HEALTHY
                timeout_s = wave_spec.timeout_s if wave_spec else 120.0
                name = wave_spec.name if wave_spec else f"wave-{idx}"

                self._artifacts.milestone(
                    self._ctx,
                    f"starting wave {name}: {', '.join(wave)}",
                    fraction=idx / max(total, 1),
                )
                self._backend.up(
                    spec,
                    list(wave),
                    wait=wait,
                    timeout_s=timeout_s,
                    cwd=workdir,
                )
                self._attach_service_logs(spec, list(wave), cwd=workdir)
                result.waves_completed.append(name)
                self._artifacts.milestone(self._ctx, f"wave ready: {name}")

            self._artifacts.milestone(self._ctx, "stack up complete", fraction=1.0)
            return result
        except Exception:
            self._teardown(spec, policy, cwd=workdir)
            raise

    def down(self, spec: StackSpec, *, cwd: Path | None = None) -> None:
        policy = TeardownPolicy(spec.teardown)
        self._teardown(spec, policy, cwd=cwd or Path.cwd())

    def _attach_service_logs(
        self,
        spec: StackSpec,
        services: list[str],
        *,
        cwd: Path,
    ) -> None:
        logs = self._backend.service_logs(spec, services, cwd=cwd)
        for service, content in logs.items():
            log_path = self._ctx.artifact(f"docker/{service}.log")
            log_path.write_text(content, encoding="utf-8")
            self._artifacts.attach_file(self._ctx, log_path, name=f"{service}.log")

    def _teardown(self, spec: StackSpec, policy: TeardownPolicy, *, cwd: Path) -> None:
        if policy == TeardownPolicy.NONE:
            return
        try:
            if policy == TeardownPolicy.STOP:
                self._backend.stop(spec, cwd=cwd)
                self._ctx.log("teardown: compose stop complete")
                return
            remove_volumes = policy == TeardownPolicy.DOWN
            self._backend.down(spec, cwd=cwd, remove_volumes=remove_volumes)
            self._ctx.log("teardown: compose down complete")
        except Exception as exc:  # noqa: BLE001 — best-effort teardown
            self._ctx.log(f"teardown warning: {exc}")


def _wave_for_services(spec: StackSpec, wave: tuple[str, ...]) -> Any:
    wave_set = set(wave)
    for wave_spec in spec.waves:
        if set(wave_spec.services) == wave_set:
            return wave_spec
    return None
