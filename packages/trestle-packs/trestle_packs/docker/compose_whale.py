"""python-on-whales compose backend."""

from __future__ import annotations

import shutil
from pathlib import Path

from trestle_packs.core.readiness import PollConfig, poll_until, run_probe
from trestle_packs.docker.spec import StackSpec, WaitMode


class WhaleComposeBackend:
    def is_available(self) -> bool:
        return shutil.which("docker") is not None

    def _client(self, spec: StackSpec, *, cwd: Path):
        from python_on_whales import DockerClient

        compose_file = spec.compose_path(cwd=cwd)
        if not compose_file.exists():
            msg = f"compose file not found: {compose_file}"
            raise FileNotFoundError(msg)
        return DockerClient(
            compose_files=[compose_file],
            compose_project_name=spec.project,
        )

    def up(
        self,
        spec: StackSpec,
        services: list[str],
        *,
        wait: WaitMode,
        timeout_s: float,
        cwd: Path,
    ) -> None:
        client = self._client(spec, cwd=cwd)
        wait_flag = wait in {WaitMode.HEALTHY, WaitMode.STARTED}
        client.compose.up(
            services,
            build=False,
            detach=True,
            wait=wait_flag and wait == WaitMode.HEALTHY,
            wait_timeout=int(timeout_s) if wait_flag else None,
        )

        if wait == WaitMode.CUSTOM:
            for service in services:
                probe = spec.probes.get(service)
                if probe is None or not probe.command:
                    msg = f"custom wait requires probe for service: {service}"
                    raise ValueError(msg)
                poll_until(
                    lambda cmd=probe.command: run_probe(cmd),
                    config=PollConfig(timeout_s=timeout_s),
                )

    def down(self, spec: StackSpec, *, cwd: Path, remove_volumes: bool = False) -> None:
        client = self._client(spec, cwd=cwd)
        client.compose.down(volumes=remove_volumes)

    def stop(self, spec: StackSpec, *, cwd: Path) -> None:
        client = self._client(spec, cwd=cwd)
        client.compose.stop()

    def service_logs(
        self,
        spec: StackSpec,
        services: list[str],
        *,
        cwd: Path,
    ) -> dict[str, str]:
        client = self._client(spec, cwd=cwd)
        logs: dict[str, str] = {}
        for service in services:
            try:
                output = client.compose.logs(services=[service])
            except Exception as exc:  # noqa: BLE001 — best-effort log capture
                logs[service] = f"(log fetch failed: {exc})\n"
                continue
            logs[service] = output if isinstance(output, str) else str(output)
        return logs
