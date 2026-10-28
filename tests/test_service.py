"""`trestle service` (decision record service-and-credentials, change 2).

Unit generation is checked for both platforms on any host. The lifecycle runs against a fake
service manager (`launchctl` or `systemctl --user`) that does what the real one does with the
unit it was given: it starts the unit's own `ProgramArguments` / `ExecStart` with exactly the
unit's environment, restarts it after a crash (`KeepAlive = {SuccessfulExit = false}`,
`Restart=on-failure`) and never after a clean exit. So the server under test is a real
`trestle serve`: its drain, its exit and its runs are real; only the service manager is faked.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.proof import tolerances
from tests.proof.mcp_host import agent_token
from trestle.server.service import (
    LAUNCHD_LABEL,
    MARK,
    SYSTEMD_UNIT,
    Launchd,
    Service,
    ServiceError,
    SystemdUser,
    UnitSpec,
)

REPO = Path(__file__).resolve().parents[1]
SPEC = UnitSpec(
    binary=Path("/opt/trestle venv/bin/trestle"),
    port=18732,
    trestle_home=Path("/Users/someone/.trestle"),
    user_home=Path("/Users/someone"),
    path="/opt/trestle venv/bin:/usr/bin:/bin",
)


# --- unit generation ----------------------------------------------------------------------------


def test_launchd_plist() -> None:
    text = Launchd(run=_no_commands).render(SPEC)
    assert MARK in text
    plist = plistlib.loads(text.encode("utf-8"))
    assert plist["Label"] == LAUNCHD_LABEL
    assert plist["ProgramArguments"] == [str(SPEC.binary), "serve", "--port", "18732"]
    # exactly PATH, HOME and TRESTLE_HOME: nothing inherited from the installing shell
    assert plist["EnvironmentVariables"] == {
        "PATH": SPEC.path,
        "HOME": "/Users/someone",
        "TRESTLE_HOME": "/Users/someone/.trestle",
    }
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}  # a crash restarts, a drain does not
    assert plist["StandardErrorPath"] == "/Users/someone/.trestle/service.log"
    assert Launchd(run=_no_commands).parse(text) == SPEC


def test_systemd_unit() -> None:
    text = SystemdUser(run=_no_commands).render(SPEC)
    lines = text.splitlines()
    assert lines[0].startswith(f"# {MARK}")
    exec_start = next(line for line in lines if line.startswith("ExecStart="))
    assert shlex.split(exec_start.removeprefix("ExecStart=")) == [
        str(SPEC.binary),
        "serve",
        "--port",
        "18732",
    ]
    env = next(line for line in lines if line.startswith("Environment="))
    assert dict(item.split("=", 1) for item in shlex.split(env.removeprefix("Environment="))) == {
        "PATH": SPEC.path,
        "HOME": "/Users/someone",
        "TRESTLE_HOME": "/Users/someone/.trestle",
    }
    for expected in (
        "Restart=on-failure",
        "KillMode=mixed",
        "KillSignal=SIGTERM",
        "TimeoutStopSec=infinity",
        "WantedBy=default.target",
    ):
        assert expected in lines, expected
    assert SystemdUser(run=_no_commands).parse(text) == SPEC


def test_systemd_unit_escapes_percent() -> None:
    spec = UnitSpec(
        binary=Path("/opt/100%/bin/trestle"),
        port=1,
        trestle_home=Path("/h/.trestle"),
        user_home=Path("/h"),
        path="/usr/bin",
    )
    text = SystemdUser(run=_no_commands).render(spec)
    assert "/opt/100%%/bin/trestle" in text
    assert SystemdUser(run=_no_commands).parse(text) == spec


@pytest.mark.parametrize("platform", ["launchd", "systemd"])
def test_install_refuses_a_unit_it_did_not_write(tmp_path: Path, platform: str) -> None:
    manager = FakeManager(platform, tmp_path)
    service = manager.service()
    service.unit.parent.mkdir(parents=True)
    service.unit.write_text("[Service]\nExecStart=/usr/bin/something-else\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="not written by trestle"):
        service.install()
    with pytest.raises(ServiceError, match="not written by trestle"):
        service.uninstall()
    assert service.unit.read_text(encoding="utf-8").endswith("something-else\n")
    assert manager.commands == []


# --- the lifecycle against a fake service manager -----------------------------------------------


def _no_commands(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    raise AssertionError(f"no command expected: {argv}")


class FakeManager:
    """`launchctl` or `systemctl --user`, for one unit, in process. Every other command (the
    binary's `--version`, the migration, pip) runs for real except pip, which is recorded."""

    def __init__(self, platform: str, root: Path) -> None:
        self.platform_name = platform
        self.root = root
        self.user_home = root / "user"
        self.trestle_home = self.user_home / ".trestle"
        self.binary = _wrapper(root / "venv" / "bin")
        self.commands: list[list[str]] = []
        self.installs: list[list[str]] = []
        self.lines: list[str] = []
        self.loaded = False
        self.enabled = True
        self.unit_file: Path | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.spawned = 0
        self.platform = (
            Launchd(run=self, uid=os.getuid()) if platform == "launchd" else SystemdUser(run=self)
        )

    def service(self, **kw: Any) -> Service:
        return Service(
            platform=self.platform,
            user_home=self.user_home,
            trestle_home=self.trestle_home,
            port=0,  # the server picks a free port and its lock says which
            binary=self.binary,
            run=self,
            install_command=["pip", "install", "-U", "trestle"],
            path_env="/usr/bin:/bin",
            out=lambda line: self.lines.append(line),
            **kw,
        )

    # -- the process the unit runs ------------------------------------------------------------

    def _spec(self) -> UnitSpec:
        assert self.unit_file is not None
        return self.platform.parse(self.unit_file.read_text(encoding="utf-8"))

    def _spawn(self) -> None:
        spec = self._spec()
        spec.trestle_home.mkdir(parents=True, exist_ok=True)
        log = open(spec.log, "ab")  # noqa: SIM115 - handed to the child
        self.proc = subprocess.Popen(
            spec.argv, env=spec.env, cwd=spec.trestle_home, stdout=log, stderr=log
        )
        log.close()
        self.spawned += 1

    def _reap(self) -> None:
        """What the manager does when the process has exited: restart it after a crash (non-zero
        exit) while loaded, forget it after a clean exit."""
        if self.proc is not None and self.proc.poll() is not None:
            crashed = self.proc.returncode != 0
            self.proc = None
            if crashed and self.loaded:
                self._spawn()

    def changes(self) -> list[list[str]]:
        """The commands sent so far that change something (every one but a read)."""
        verbs = [(c, c[2] if c[0] == "systemctl" else c[1]) for c in self.commands]
        return [c for c, verb in verbs if verb not in {"print", "show"}]

    @property
    def pid(self) -> int | None:
        self._reap()
        return self.proc.pid if self.proc is not None else None

    def kill_hard(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    # -- the commands ------------------------------------------------------------------------

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        if argv[0] == "pip":
            self.installs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] not in {"launchctl", "systemctl"}:
            return subprocess.run(argv, capture_output=True, text=True, check=False)
        self.commands.append(argv)
        rc, out = self._launchctl(argv[1:]) if argv[0] == "launchctl" else self._systemctl(argv[2:])
        return subprocess.CompletedProcess(argv, rc, out, "" if rc == 0 else out)

    def _launchctl(self, args: list[str]) -> tuple[int, str]:
        verb = args[0]
        if verb == "print":
            if not self.loaded:
                return 113, "Could not find service"
            pid = self.pid
            state = f"\tstate = running\n\tpid = {pid}\n" if pid else "\tstate = not running\n"
            return 0, f"{LAUNCHD_LABEL} = {{\n{state}}}\n"
        if verb == "bootstrap":
            if not self.enabled:
                return 5, "Bootstrap failed: 5: Input/output error (disabled)"
            if self.loaded:
                return 37, "Bootstrap failed: 37: Operation already in progress"
            self.unit_file = Path(args[2])
            self.loaded = True
            self._spawn()  # RunAtLoad
            return 0, ""
        if verb == "bootout":
            if not self.loaded:
                return 3, "Boot-out failed: 3: No such process"
            self.loaded = False
            self._stop_process()
            return 0, ""
        if verb == "kickstart":
            if not self.loaded:
                return 113, "Could not find service"
            if self.pid is None:
                self._spawn()
            return 0, ""
        if verb == "kill":
            if self.pid is not None:
                os.kill(self.pid, getattr(signal, args[1]))
            return 0, ""
        if verb in {"enable", "disable"}:
            self.enabled = verb == "enable"
            return 0, ""
        return 64, f"unknown launchctl verb {verb}"

    def _systemctl(self, args: list[str]) -> tuple[int, str]:
        verb = args[0]
        unit = self.user_home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
        if verb == "daemon-reload":
            return 0, ""
        if verb == "show":
            state = "enabled" if self.loaded else "disabled"
            return 0, f"UnitFileState={state}\nMainPID={self.pid or 0}\n"
        if verb == "enable":
            self.unit_file = unit
            self.loaded = True
            return 0, ""
        if verb == "disable":
            self.loaded = False
            return 0, ""
        if verb == "start":
            self.unit_file = unit
            if self.pid is None:
                self._spawn()
            return 0, ""
        if verb == "stop":
            self._stop_process()
            return 0, ""
        if verb == "kill":
            if self.pid is not None:
                os.kill(self.pid, signal.SIGTERM)
            return 0, ""
        return 64, f"unknown systemctl verb {verb}"

    def _stop_process(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=tolerances.JOIN_WAIT_S)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def _wrapper(bin_dir: Path) -> Path:
    """The `trestle` the unit runs: this checkout's CLI under this interpreter. The unit's
    environment is exactly PATH, HOME and TRESTLE_HOME, so the wrapper names its own
    interpreter and source tree."""
    bin_dir.mkdir(parents=True)
    script = bin_dir / "trestle"
    script.write_text(
        "#!/bin/sh\n"
        f"PYTHONPATH={shlex.quote(str(REPO))} exec {shlex.quote(sys.executable)} "
        '-m trestle.cli "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.fixture(params=["launchd", "systemd"])
def manager(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[FakeManager]:
    fake = FakeManager(request.param, tmp_path)
    try:
        yield fake
    finally:
        fake.kill_hard()


def _call(home: Path, port: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One MCP tool call over the service's endpoint with the home's token."""
    import asyncio

    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    async def call() -> dict[str, Any]:
        transport = StreamableHttpTransport(
            url=f"http://127.0.0.1:{port}/mcp",
            headers={"Authorization": f"Bearer {agent_token(home)}"},
        )
        async with Client(transport=transport) as client:
            return (await client.call_tool(name, arguments)).data  # type: ignore[no-any-return]

    return asyncio.run(call())


def _slow_plugin(home: Path) -> None:
    (home / "plugins" / "slow.py").write_bytes(
        (REPO / "tests" / "fixtures" / "plugins" / "slow.py").read_bytes()
    )


def test_install_runs_the_service_and_is_idempotent(manager: FakeManager) -> None:
    service = manager.service()
    assert service.install() == 0
    status = service.status()
    assert status.installed and status.ours and status.loaded and status.running
    assert status.spec is not None
    # the binary's directory first, the installing shell's PATH, then the system directories
    assert status.spec.path == os.pathsep.join(
        [str(manager.binary.parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    )
    assert status.endpoint is not None and status.endpoint.endswith("/mcp")
    assert status.spec is not None and status.spec.trestle_home == manager.trestle_home
    assert status.running_build == status.installed_build is not None
    assert not status.stale
    assert (manager.trestle_home / "agent.token").stat().st_mode & 0o777 == 0o600
    assert status.port is not None
    assert _call(manager.trestle_home, status.port, "list_plugins", {})["items"] is not None

    unit_before = service.unit.read_text(encoding="utf-8")
    commands_before = manager.changes()
    pid_before = status.pid
    assert service.install() == 0  # again: reports the state, changes nothing
    assert "already installed" in manager.lines[-1]
    assert service.unit.read_text(encoding="utf-8") == unit_before
    assert manager.changes() == commands_before
    assert service.status().pid == pid_before
    assert manager.spawned == 1


def test_stop_drains_and_stays_down_then_start(manager: FakeManager) -> None:
    service = manager.service()
    service.install()
    status = service.status()
    assert status.port is not None
    _slow_plugin(manager.trestle_home)
    run = _call(
        manager.trestle_home,
        status.port,
        "run",
        {"plugin": "slow", "args": {"seconds": 2.0}, "wait_ms": 0},
    )
    assert run["state"] in {"queued", "running"}, run
    old = manager.proc
    assert old is not None

    assert service.stop() == 0
    # the drain finished the run, the server exited 0 on its own, and nothing revived it
    assert old.returncode == 0
    assert "drained 1 live run" in manager.lines[-1]
    time.sleep(tolerances.SETTLE_LONG_S)
    after = service.status()
    assert not after.running and not after.loaded and manager.proc is None
    assert manager.spawned == 1
    assert after.live_runs == 0
    from tests.proof import records

    (run_dir,) = (manager.trestle_home / "runs").glob(f"*/{run['run_id']}")
    assert records.node_record(run_dir).terminal == "succeeded"

    assert service.start() == 0
    assert service.status().running and manager.spawned == 2
    assert service.restart() == 0
    assert service.status().running and manager.spawned == 3
    assert service.uninstall() == 0
    assert not service.unit.exists() and manager.proc is None


def test_a_crash_is_restarted(manager: FakeManager) -> None:
    service = manager.service()
    service.install()
    first = service.status().pid
    assert first is not None
    os.kill(first, signal.SIGKILL)
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while (manager.pid in {None, first}) and time.monotonic() < deadline:
        time.sleep(tolerances.POLL_S)
    assert manager.pid not in {None, first}
    assert manager.spawned == 2


def test_upgrade_refuses_with_a_live_run_and_drains_with_drain(manager: FakeManager) -> None:
    service = manager.service()
    service.install()
    status = service.status()
    assert status.port is not None
    _slow_plugin(manager.trestle_home)
    run = _call(
        manager.trestle_home,
        status.port,
        "run",
        {"plugin": "slow", "args": {"seconds": 3.0}, "wait_ms": 0},
    )
    pid = status.pid

    with pytest.raises(ServiceError, match="1 live run.*--drain"):
        service.upgrade()
    assert manager.installs == []  # nothing changed: no install, no restart
    assert service.status().pid == pid

    assert service.upgrade(drain=True) == 0
    assert manager.installs == [["pip", "install", "-U", "trestle"]]
    after = service.status()
    assert after.running and after.pid != pid
    assert "upgraded" in manager.lines[-1]
    from tests.proof import records

    (run_dir,) = (manager.trestle_home / "runs").glob(f"*/{run['run_id']}")
    assert records.node_record(run_dir).terminal == "succeeded"  # drained, not interrupted


def test_upgrade_with_no_live_run_restarts_onto_the_new_build(manager: FakeManager) -> None:
    service = manager.service()
    service.install()
    pid = service.status().pid
    assert service.upgrade() == 0
    assert manager.installs == [["pip", "install", "-U", "trestle"]]
    assert service.status().pid not in {None, pid}


def test_install_with_a_newer_build_behaves_as_upgrade(manager: FakeManager) -> None:
    """The running server's build differs from what the binary now reports: install restarts
    onto it (draining), without running pip."""
    service = manager.service()
    service.install()
    pid = service.status().pid
    lock = next((manager.trestle_home / "servers").glob("*.lock"))
    # the running server reports an older build in its lock file
    import json

    data = json.loads(lock.read_text(encoding="utf-8"))
    data["version"] = "0.0.1"
    os.truncate(lock, 0)
    with open(lock, "r+", encoding="utf-8") as handle:
        handle.write(json.dumps(data))
    assert service.status().stale
    assert service.install() == 0
    assert "upgraded 0.0.1 ->" in manager.lines[-1]
    assert manager.installs == []
    status = service.status()
    assert status.pid not in {None, pid} and not status.stale


def test_status_reports_without_a_unit(tmp_path: Path) -> None:
    manager = FakeManager("launchd", tmp_path)
    status = manager.service().status()
    assert not status.installed and not status.running
    assert "installed: no" in status.lines()[0]


def test_cli_service_status_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trestle import cli
    from trestle.server import service as service_mod

    manager = FakeManager("launchd", tmp_path)
    monkeypatch.setattr(service_mod, "default_platform", lambda run=None: manager.platform)
    monkeypatch.setattr(service_mod.Path, "home", staticmethod(lambda: manager.user_home))
    assert cli.main(["service", "status"]) == 3  # not running
    assert cli.main(["service", "stop"]) == 1  # not installed
