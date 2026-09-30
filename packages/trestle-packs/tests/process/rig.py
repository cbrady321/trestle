"""Bindings of the local-process restart family (L.RB-8.1): the fake local process and the real
`LocalProcessPort` launching the stdlib override app (`tests/fixtures/apps/override_app.py`) as a
real child. The family's cases (`tests/conformance/process_restart_cases.py`) run UNMODIFIED
against both; what they need beyond the port is the fixture contract in that module's docstring.
Nothing here touches Docker: the real binding is PROC, venue BOTH (CI and `host-proc`).
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from tests.proof import tolerances
from tests.proof.suites.ports import core
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind

from trestle_packs.fakes.local_process import FakeLocalProcess
from trestle_packs.process import identity
from trestle_packs.process.local import LocalProcessPort, found_selector

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "tests" / "fixtures" / "apps" / "override_app.py"
START_WAIT_S = tolerances.JOIN_WAIT_S  # an app that has not said `start` by then never will


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def spec_for(executable: str, app: str, environment: dict[str, str]) -> ports.ResourceSpec:
    resolved = ports.Resolved(executable, "3.12", "pin", "adoption")
    command = ports.BoundCommand("app", (executable, app), environment, resolved, False)
    return ports.ResourceSpec(
        "suite-app", RealizationKind.AGENT_LAUNCHED_PROJECT, "suite-entry", command
    )


# ------------------------------------------------------------------------------- fake


def fake_restart(
    fake_class: type[FakeLocalProcess] = FakeLocalProcess,
) -> Callable[[Path], core.Implementation]:
    def build(base: Path) -> core.Implementation:
        fake = fake_class()
        spec = spec_for("/suite/bin/python", "override_app.py", {"PORT": "20421"})
        assert spec.command is not None
        argv = spec.command.argv
        return core.Implementation(
            fake,
            core.Reach(engine_inventory=fake.inventory),
            name="fake-local-restart",
            extras={
                "spec": spec,
                "plant_found": lambda system: fake.plant_found(system, argv),
                "token_of": fake.token_of,
                "alive": fake.alive,
                "in_run_group": fake.in_run_group,
                "kill": fake.kill,
                "events": lambda starts: fake.events(),
                "break_launch": fake.break_launch,
            },
            close=fake.close,
        )

    return build


# ------------------------------------------------------------------------------- real


def real_restart(
    port_class: type[LocalProcessPort] = LocalProcessPort,
) -> Callable[[Path], core.Implementation]:
    counter = iter(range(10_000))

    def build(base: Path) -> core.Implementation:
        directory = base / f"app-{next(counter)}"
        directory.mkdir()
        interpreter = directory / "python"  # a path this case can remove: the bound command's exe
        os.symlink(sys.executable, interpreter)
        log = directory / "events.log"
        env = {"PORT": str(free_port()), "APP_EVENT_LOG": str(log)}
        spec = spec_for(str(interpreter), str(APP), env)
        assert spec.command is not None
        argv = spec.command.argv
        port = port_class()
        planted: list[subprocess.Popen[bytes]] = []

        def plant_found(system: str) -> str:
            # a process that runs the spec's command line and is not this port's: found. It listens
            # on another port and logs nothing to the app's event log.
            helper = subprocess.Popen(  # noqa: S603 - the suite's own fixed program
                argv,
                env={"PORT": str(free_port()), "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            planted.append(helper)
            deadline = time.monotonic() + START_WAIT_S
            start = identity.start_time(helper.pid)
            while start is None and time.monotonic() < deadline:
                time.sleep(tolerances.POLL_FINE_S)
                start = identity.start_time(helper.pid)
            assert start is not None
            return found_selector(helper.pid, start)

        def token_of(selector: str) -> str | None:
            instance = port._instances.get(selector)
            if instance is not None:
                return f"{instance.proc.pid}-{instance.start}"
            parts = selector.split("-")
            return f"{parts[1]}-{parts[2]}" if len(parts) == 3 and parts[0] == "found" else None

        def alive(token: str) -> bool:
            pid, _, start = token.partition("-")
            return identity.start_time(int(pid)) == int(start)

        def in_run_group(token: str) -> bool:
            pid = int(token.partition("-")[0])
            return os.getpgid(pid) == os.getpgid(0) and os.getsid(pid) == os.getsid(0)

        def kill(selector: str) -> None:
            instance = port._instances[selector]
            os.kill(instance.proc.pid, signal.SIGKILL)
            instance.proc.wait()

        def events(starts: int) -> list[str]:
            deadline = time.monotonic() + START_WAIT_S
            lines: list[str] = []
            while time.monotonic() < deadline:
                lines = log.read_text().split() if log.exists() else []
                if lines.count("start") >= starts:
                    break
                time.sleep(tolerances.POLL_FINE_S)
            return lines

        def close() -> None:
            port.close()
            for helper in planted:
                helper.kill()
                helper.wait()

        return core.Implementation(
            port,
            core.Reach(engine_inventory=port.inventory),
            name="real-local-restart",
            extras={
                "spec": spec,
                "plant_found": plant_found,
                "token_of": token_of,
                "alive": alive,
                "in_run_group": in_run_group,
                "kill": kill,
                "events": events,
                "break_launch": lambda: interpreter.unlink(),
            },
            close=close,
        )

    return build
