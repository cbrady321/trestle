"""L.NW-2.9: WR-CANCEL-5, the adapter contract-suite half (B3-C14, MC-12, MC-13, MC-09).

Every docker subprocess the adapter starts gets closed stdin, reads no transport bytes and dies on
cancel. The adapter starts docker only through the injected `ExecutionPort`; here the real
`CommandPort` runs inside a run of the MCP test host (`trestle serve` over its stdio transport, the
`docker_adapter_probe` plugin), against the absolute-path `fake_docker.py` shim in its
`stdin-read` mode (which reads its stdin to EOF and logs the bytes it got) and its `hang` mode
(which never returns). The family suite's `a_root_cancel_...` case runs the same contract on the
fake and the real bindings.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from tests.core.spine import support
from tests.proof import ancestry, mcp_host, tolerances
from tests.proof.host.docker_gate import fake_docker

PLUGIN = Path(__file__).resolve().parent / "plugins" / "docker_adapter_probe.py"
REPO = Path(__file__).resolve().parents[4]
PACKS = REPO / "packages" / "trestle-packs"
ENDPOINT = "unix:///fake/desktop-linux.sock"


def _shim(directory: Path, mode: str) -> tuple[str, Path]:
    directory.mkdir()
    state, log = directory / "state.json", directory / "log.jsonl"
    fake_docker.write_state(
        state, reachable=True, server_version="29.8.0", containers=[], images=[], volumes=[]
    )
    return fake_docker.install_shim(directory, state, log, mode=mode), log


def _pids() -> set[int]:
    return {p.pid for p in ancestry.snapshot()}


@pytest.mark.proves("WR-CANCEL-5", "WR-CANCEL-5:adapter-contract-suite", "core", "B", "STUB", "CI")
def test_adapter_subprocess_no_transport_bytes_and_dies_on_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the server and its plugin children import this checkout's `trestle` and `trestle_packs`, also
    # when the session's own PYTHONPATH names only the packs directory (the scrubbed-env session)
    inherited = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(p for p in (str(REPO), str(PACKS), inherited) if p)
    )
    reader_shim, reader_log = _shim(tmp_path / "reader", "stdin-read")
    hanger_shim, hanger_log = _shim(tmp_path / "hanger", "hang")
    with support.reaping(reader_shim), support.reaping(hanger_shim):
        with mcp_host.McpHost(home=tmp_path / "home") as host:
            shutil.copy(PLUGIN, host.home / "plugins")
            # 1. closed stdin, no transport bytes: the shim reads EOF at its first read
            done = host.call(
                "run",
                {
                    "plugin": "docker_adapter_probe",
                    "args": {"shim": reader_shim, "endpoint": ENDPOINT, "reads": 2},
                    "wait_ms": tolerances.HARNESS_WAIT_MS,
                },
            )
            assert done.get("state") == "succeeded", done
            calls = fake_docker.read_log(reader_log)
            assert len(calls) >= 2 and all(c["host"] == ENDPOINT for c in calls)
            assert {c["stdin"] for c in calls} <= {"eof", "closed"}  # EOF, never a tty or data
            assert [c["stdin_bytes"] for c in calls] == [""] * len(calls)  # zero bytes delivered
            # the transport is intact: a request after the shim ran is answered, and counted once
            sent = host.request_count()
            after = host.call("run", {"plugin": "echo", "args": {"message": "after"}})
            assert after["state"] in ("running", "succeeded"), after
            assert host.request_count() == sent + 1

            # 2. dies on cancel: the shim hangs inside a run; a root cancel ends it
            started = host.call(
                "run",
                {
                    "plugin": "docker_adapter_probe",
                    "args": {"shim": hanger_shim, "endpoint": ENDPOINT},
                },
            )
            assert started["state"] == "running", started
            assert support.wait_until(
                lambda: bool(fake_docker.read_log(hanger_log)), tolerances.JOIN_WAIT_S
            ), "the adapter never started the docker shim"
            shim_pid = fake_docker.read_log(hanger_log)[0]["pid"]
            assert shim_pid in _pids()  # alive: it is blocked, not finished
            host.call("cancel", {"run_id": started["run_id"]})
            # MC-13 snapshot: gone within the MC-09 cancel bound (plus the tolerance the other
            # containment tests allow)
            assert support.wait_until(
                lambda: shim_pid not in _pids(), tolerances.stop_bound() + tolerances.PROC_WAIT_S
            ), f"docker shim {shim_pid} outlived the cancel"


def test_the_adapter_hands_the_ports_the_root_cancel_and_a_bounded_deadline(tmp_path: Path) -> None:
    """Every call passes the root's cancel signal and an `until` a bounded time ahead, so the port
    can end the process at either (B3-C14): no docker call is left to run unbounded."""
    from datetime import UTC, datetime

    from trestle_packs.container import bind
    from trestle_packs.container.engine import CALL_BOUND, NEVER_CANCEL
    from trestle_packs.fakes import FakeCommand, passed_result

    seen: list[tuple[Any, datetime]] = []

    class Spy(FakeCommand):
        def run(self, command: Any, ticket: Any, cancel: Any, until: datetime) -> Any:
            seen.append((cancel, until))
            return super().run(command, ticket, cancel, until)

    class Root:
        requested = False

        def cause(self) -> None:
            return None

        def wait(self, timeout: Any) -> bool:
            return False

    root = Root()
    spec = type("Spec", (), {"logical_system": "suite-db", "entry": "e", "command": None})()
    lineage = type("L", (), {"root_run_id": "r_x", "path": type("P", (), {"segments": ("a",)})()})()
    for cancel in (root, None):
        seen.clear()
        port = bind(
            str(tmp_path / "docker"), None, Spy({"docker.ps": passed_result()}), cancel=cancel
        ).containers
        port.observe(spec, lineage, "up")  # type: ignore[arg-type]
        assert seen and all(c is (root if cancel is root else NEVER_CANCEL) for c, _ in seen)
        now = datetime.now(UTC)
        assert all(now < until <= now + CALL_BOUND for _, until in seen)
