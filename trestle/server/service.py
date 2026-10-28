"""`trestle service`: Trestle as one per-user service per machine (decision record
service-and-credentials, Decision 1, "The CLI").

A macOS LaunchAgent in `~/Library/LaunchAgents` or a `systemd --user` unit, never a root or system
daemon. The unit runs the absolute path of the `trestle` binary that installed it, `serve` on the
loopback port, with an environment of exactly `PATH`, `HOME` and `TRESTLE_HOME`. A crash restarts
it; a clean exit does not: SIGTERM makes the server drain and exit 0 once it owns no live run, so
`stop` (and an upgrade) waits out the runs instead of interrupting them, and the drained server is
not brought back.

Every service-manager command goes through one `Runner` (argv in, completed process out), so tests
drive the whole lifecycle against a fake `launchctl` or `systemctl`.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from trestle.common.bind import LOOPBACK_HOST
from trestle.server.agent_token import ensure_token, token_path
from trestle.server.home import check_home, live_run_ids, live_servers

LAUNCHD_LABEL = "dev.trestle.service"
SYSTEMD_UNIT = "trestle.service"
MARK = "written by `trestle service install`"
POLL_S = 0.25
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
START_WAIT_S = 60.0

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


class ServiceError(Exception):
    """A refusal or failure, reported in one line with exit code `code`."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UnitSpec:
    """What a unit says: the binary it runs, the port it serves, and its three variables."""

    binary: Path
    port: int
    trestle_home: Path
    user_home: Path
    path: str

    @property
    def argv(self) -> list[str]:
        return [str(self.binary), "serve", "--port", str(self.port)]

    @property
    def env(self) -> dict[str, str]:
        return {
            "PATH": self.path,
            "HOME": str(self.user_home),
            "TRESTLE_HOME": str(self.trestle_home),
        }

    @property
    def log(self) -> Path:
        return self.trestle_home / "service.log"


@dataclass(frozen=True)
class Probe:
    loaded: bool
    pid: int | None


class Platform(Protocol):
    name: str

    def unit_path(self, user_home: Path) -> Path: ...
    def render(self, spec: UnitSpec) -> str: ...
    def parse(self, text: str) -> UnitSpec: ...
    def probe(self) -> Probe: ...
    def load(self, unit: Path) -> None: ...
    def unload(self) -> None: ...
    def kick(self) -> None: ...
    def terminate(self) -> None: ...
    def enable(self) -> None: ...
    def disable(self) -> None: ...


def _check(result: subprocess.CompletedProcess[str], what: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ServiceError(f"{what} failed ({result.returncode}): {detail[-1] if detail else ''}")


# --- launchd ------------------------------------------------------------------------------------


@dataclass
class Launchd:
    """A LaunchAgent in the user's GUI domain. `KeepAlive` restarts it after a crash only
    (`SuccessfulExit = false`): a drained server exits 0 and stays down."""

    run: Runner = run_command
    uid: int = field(default_factory=os.getuid)
    name: str = "launchd"

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    @property
    def target(self) -> str:
        return f"{self.domain}/{LAUNCHD_LABEL}"

    def unit_path(self, user_home: Path) -> Path:
        return user_home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

    def render(self, spec: UnitSpec) -> str:
        body = plistlib.dumps(
            {
                "Label": LAUNCHD_LABEL,
                "ProgramArguments": spec.argv,
                "EnvironmentVariables": spec.env,
                "WorkingDirectory": str(spec.trestle_home),
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "ProcessType": "Background",
                "StandardOutPath": str(spec.log),
                "StandardErrorPath": str(spec.log),
            }
        ).decode("utf-8")
        head, _, rest = body.partition("<plist")
        comment = (
            f"<!-- {MARK}. Edit freely; install refuses to overwrite a plist without this line. -->"
        )
        return f"{head}{comment}\n<plist{rest}"

    def parse(self, text: str) -> UnitSpec:
        data = plistlib.loads(text.encode("utf-8"))
        return _spec_from(list(data.get("ProgramArguments", [])), data.get("EnvironmentVariables"))

    def probe(self) -> Probe:
        result = self.run(["launchctl", "print", self.target])
        if result.returncode != 0:
            return Probe(loaded=False, pid=None)
        pid = None
        for line in result.stdout.splitlines():
            key, _, value = line.strip().partition(" = ")
            if key == "pid" and value.strip().isdigit():
                pid = int(value)
                break
        return Probe(loaded=True, pid=pid)

    def load(self, unit: Path) -> None:
        _check(self.run(["launchctl", "bootstrap", self.domain, str(unit)]), "launchctl bootstrap")

    def unload(self) -> None:
        _check(self.run(["launchctl", "bootout", self.target]), "launchctl bootout")

    def kick(self) -> None:
        _check(self.run(["launchctl", "kickstart", self.target]), "launchctl kickstart")

    def terminate(self) -> None:
        _check(self.run(["launchctl", "kill", "SIGTERM", self.target]), "launchctl kill")

    def enable(self) -> None:
        _check(self.run(["launchctl", "enable", self.target]), "launchctl enable")

    def disable(self) -> None:
        _check(self.run(["launchctl", "disable", self.target]), "launchctl disable")


# --- systemd --user -----------------------------------------------------------------------------


def _systemd_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


@dataclass
class SystemdUser:
    """A `systemd --user` unit. `Restart=on-failure` is `KeepAlive`'s twin (a drained exit 0
    stays down); `KillMode=mixed` sends the stop's SIGTERM to the server only, never to the runs'
    processes, and `TimeoutStopSec=infinity` lets the drain take as long as the runs do."""

    run: Runner = run_command
    name: str = "systemd"

    def _ctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run(["systemctl", "--user", *args])

    def unit_path(self, user_home: Path) -> Path:
        return user_home / ".config" / "systemd" / "user" / SYSTEMD_UNIT

    def render(self, spec: UnitSpec) -> str:
        env = " ".join(_systemd_quote(f"{k}={v}") for k, v in spec.env.items())
        return "\n".join(
            [
                f"# {MARK}. Edit freely; install refuses to overwrite a unit without this line.",
                "[Unit]",
                "Description=Trestle MCP service",
                "",
                "[Service]",
                "Type=exec",
                f"ExecStart={' '.join(_systemd_quote(arg) for arg in spec.argv)}",
                f"Environment={env}",
                f"WorkingDirectory={_systemd_quote(str(spec.trestle_home))}",
                "Restart=on-failure",
                "KillMode=mixed",
                "KillSignal=SIGTERM",
                "TimeoutStopSec=infinity",
                f"StandardOutput=append:{spec.log}",
                f"StandardError=append:{spec.log}",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )

    def parse(self, text: str) -> UnitSpec:
        argv: list[str] = []
        env: dict[str, str] = {}
        for line in text.splitlines():
            key, _, value = line.partition("=")
            if key == "ExecStart":
                argv = [arg.replace("%%", "%") for arg in shlex.split(value)]
            elif key == "Environment":
                for item in shlex.split(value):
                    name, _, val = item.replace("%%", "%").partition("=")
                    env[name] = val
        return _spec_from(argv, env)

    def _show(self) -> dict[str, str]:
        result = self._ctl("show", SYSTEMD_UNIT, "-p", "UnitFileState", "-p", "MainPID")
        fields: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
        return fields

    def probe(self) -> Probe:
        fields = self._show()
        pid = int(fields.get("MainPID", "0") or 0)
        loaded = fields.get("UnitFileState") in {"enabled", "enabled-runtime", "linked"}
        return Probe(loaded=loaded or pid > 0, pid=pid or None)

    def load(self, unit: Path) -> None:
        _check(self._ctl("daemon-reload"), "systemctl daemon-reload")
        _check(self._ctl("enable", SYSTEMD_UNIT), "systemctl enable")
        _check(self._ctl("start", SYSTEMD_UNIT), "systemctl start")

    def unload(self) -> None:
        _check(self._ctl("stop", SYSTEMD_UNIT), "systemctl stop")
        _check(self._ctl("disable", SYSTEMD_UNIT), "systemctl disable")

    def kick(self) -> None:
        _check(self._ctl("start", SYSTEMD_UNIT), "systemctl start")

    def terminate(self) -> None:
        _check(
            self._ctl("kill", "--kill-whom=main", "--signal=SIGTERM", SYSTEMD_UNIT),
            "systemctl kill",
        )

    def enable(self) -> None:
        return None  # `load` enables

    def disable(self) -> None:
        return None  # `unload` disables


def _spec_from(argv: list[str], env: object) -> UnitSpec:
    if not isinstance(env, dict) or len(argv) < 2:
        raise ServiceError("the unit does not name a trestle binary and environment")
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 18732
    return UnitSpec(
        binary=Path(argv[0]),
        port=port,
        trestle_home=Path(str(env.get("TRESTLE_HOME", ""))),
        user_home=Path(str(env.get("HOME", ""))),
        path=str(env.get("PATH", "")),
    )


def default_platform(run: Runner = run_command) -> Platform:
    if sys.platform == "darwin":
        return Launchd(run=run)
    if sys.platform.startswith("linux"):
        return SystemdUser(run=run)
    raise ServiceError(f"no per-user service manager for {sys.platform}", code=2)


# --- the binary and its source ------------------------------------------------------------------


def trestle_binary() -> Path:
    """The absolute path of the `trestle` binary running this command."""
    argv0 = Path(sys.argv[0])
    if argv0.name == "trestle" and argv0.is_file():
        return Path(os.path.abspath(argv0))
    beside = Path(sys.executable).parent / "trestle"
    if beside.is_file():
        return beside
    found = shutil.which("trestle")
    if found:
        return Path(os.path.abspath(found))
    raise ServiceError("cannot find the `trestle` binary; run `trestle service` from it", code=2)


def default_install_command(binary: Path, source: str | None = None) -> list[str]:
    """The pip command that installs the new build into the binary's environment: the editable
    checkout again (its new commit), the URL it was installed from, or `trestle` from the index.
    `source` overrides all three."""
    python = binary.parent / "python"
    interpreter = str(python) if python.exists() else sys.executable
    if source:
        return [interpreter, "-m", "pip", "install", "-U", source]
    try:
        from importlib.metadata import distribution

        raw = distribution("trestle").read_text("direct_url.json")
        direct = json.loads(raw) if raw else {}
    except Exception:
        direct = {}
    url = str(direct.get("url", ""))
    if direct.get("dir_info", {}).get("editable") and url.startswith("file://"):
        return [interpreter, "-m", "pip", "install", "-e", url.removeprefix("file://")]
    if "vcs_info" in direct and url:
        return [interpreter, "-m", "pip", "install", "-U", f"{direct['vcs_info']['vcs']}+{url}"]
    if url:
        return [interpreter, "-m", "pip", "install", "-U", url]
    return [interpreter, "-m", "pip", "install", "-U", "trestle"]


# --- the service --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Status:
    installed: bool
    ours: bool
    loaded: bool
    pid: int | None
    spec: UnitSpec | None
    port: int | None
    installed_build: str | None
    running_build: str | None
    live_runs: int

    @property
    def running(self) -> bool:
        return self.pid is not None

    @property
    def endpoint(self) -> str | None:
        return None if self.port is None else f"http://{LOOPBACK_HOST}:{self.port}/mcp"

    @property
    def stale(self) -> bool:
        return (
            self.running_build is not None
            and self.installed_build is not None
            and self.running_build != self.installed_build
        )

    def lines(self) -> list[str]:
        home = self.spec.trestle_home if self.spec else None
        return [
            f"installed: {'yes' if self.installed else 'no'}"
            + ("" if self.ours or not self.installed else " (not written by trestle)"),
            f"loaded: {'yes' if self.loaded else 'no'}",
            f"running: {'yes' if self.running else 'no'}",
            f"pid: {self.pid if self.pid is not None else '-'}",
            f"endpoint: {self.endpoint or '-'}",
            f"home: {home or '-'}",
            f"installed build: {self.installed_build or '-'}",
            f"running build: {self.running_build or '-'}"
            + (" (stale: restart or upgrade)" if self.stale else ""),
            f"live runs: {self.live_runs}",
        ]


@dataclass
class Service:
    """The lifecycle on one platform. `user_home`, `trestle_home`, `port` and `binary` say what
    `install` writes; once a unit exists, its own content says where the service lives."""

    platform: Platform
    user_home: Path
    trestle_home: Path
    port: int
    binary: Path | None = None
    run: Runner = run_command
    install_command: list[str] | None = None
    path_env: str | None = None
    out: Callable[[str], None] = print
    start_wait_s: float = START_WAIT_S
    sleep: Callable[[float], None] = time.sleep

    # -- reading -------------------------------------------------------------------------------

    @property
    def unit(self) -> Path:
        return self.platform.unit_path(self.user_home)

    def _unit_text(self) -> str | None:
        try:
            return self.unit.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def _desired(self) -> UnitSpec:
        binary = self.binary or trestle_binary()
        path = self.path_env if self.path_env is not None else os.environ.get("PATH", "")
        parts = [p for p in path.split(os.pathsep) if p]
        if str(binary.parent) not in parts:
            parts.insert(0, str(binary.parent))
        # the server itself calls system tools by name (`sysctl`, `ps`): launchd's own default
        parts += [p for p in SYSTEM_PATH if p not in parts]
        return UnitSpec(
            binary=binary,
            port=self.port,
            trestle_home=self.trestle_home,
            user_home=self.user_home,
            path=os.pathsep.join(parts),
        )

    def _installed_build(self, binary: Path) -> str | None:
        result = self.run([str(binary), "--version"])
        text = result.stdout.strip()
        return text.split()[-1] if result.returncode == 0 and text else None

    def status(self) -> Status:
        text = self._unit_text()
        if text is None:
            return Status(False, False, False, None, None, None, None, None, 0)
        ours = MARK in text
        spec: UnitSpec | None
        try:
            spec = self.platform.parse(text)
        except Exception:
            spec = None
        probe = self.platform.probe()
        home = spec.trestle_home if spec else self.trestle_home
        server = _server_for(home, probe.pid)
        bound = server.get("port") if server else None  # the port it serves (`--port 0` too)
        port = bound if isinstance(bound, int) and bound > 0 else (spec.port if spec else None)
        return Status(
            installed=True,
            ours=ours,
            loaded=probe.loaded,
            pid=probe.pid,
            spec=spec,
            port=port,
            installed_build=self._installed_build(spec.binary) if spec else None,
            running_build=str(server["version"]) if server and server.get("version") else None,
            live_runs=len(live_run_ids(home)) if home.is_dir() else 0,
        )

    # -- the lifecycle -------------------------------------------------------------------------

    def install(self, *, drain: bool = False) -> int:
        desired = self._desired()
        text = self._unit_text()
        if text is not None and MARK not in text:
            raise ServiceError(f"{self.unit} was not written by trestle; refusing to overwrite it")
        if text is not None:
            # PATH is the installing shell's; a second install from another shell keeps the first
            desired = replace(desired, path=self.platform.parse(text).path)
        rendered = self.platform.render(desired)
        _prepare_home(desired.trestle_home)
        if text is None:
            self.unit.parent.mkdir(parents=True, exist_ok=True)
            self.unit.write_text(rendered, encoding="utf-8")
            self.platform.enable()
            self.platform.load(self.unit)
            status = self._await_running()
            self.out(f"trestle service: installed and {_running_line(status)}")
            self.out(f"agent token: {token_path(desired.trestle_home)}")
            return 0
        status = self.status()
        if text == rendered and not status.stale:
            self.out(f"trestle service: already installed; {_state_line(status)}")
            return 0
        # a changed unit or a newer build: what upgrade does, without the pip step
        return self._replace(rendered, drain=drain, install=False, before=status)

    def uninstall(self) -> int:
        text = self._unit_text()
        if text is None:
            self.out("trestle service: not installed")
            return 0
        if MARK not in text:
            raise ServiceError(f"{self.unit} was not written by trestle; refusing to remove it")
        self._drain_and_unload()
        self.unit.unlink()
        self.out(f"trestle service: uninstalled ({self.unit} removed)")
        return 0

    def start(self) -> int:
        self._require_installed()
        status = self.status()
        if status.running:
            self.out(f"trestle service: already {_running_line(status)}")
            return 0
        self._start()
        self.out(f"trestle service: {_running_line(self._await_running())}")
        return 0

    def stop(self) -> int:
        self._require_installed()
        before = self.status()
        drained = before.live_runs
        self._drain_and_unload()
        what = f"drained {drained} live run{'s' if drained != 1 else ''}" if before.running else ""
        self.out(
            f"trestle service: stopped{f' ({what})' if what else ''}; start it again with "
            "`trestle service start`"
        )
        return 0

    def restart(self) -> int:
        self._require_installed()
        self._drain_and_unload()
        self._start()
        self.out(f"trestle service: restarted; {_running_line(self._await_running())}")
        return 0

    def upgrade(self, *, drain: bool = False) -> int:
        text = self._require_installed()
        if MARK not in text:
            raise ServiceError(f"{self.unit} was not written by trestle; refusing to upgrade it")
        # the unit keeps its binary, port, home and PATH; only the build behind them changes
        rendered = self.platform.render(self.platform.parse(text))
        return self._replace(rendered, drain=drain, install=True, before=self.status())

    # -- steps ---------------------------------------------------------------------------------

    def _replace(self, rendered: str, *, drain: bool, install: bool, before: Status) -> int:
        """Stop (draining), install the new build, migrate the home, write the unit, start: the
        new build never starts while a run is live, and nothing changes when it would have to
        unless `drain`."""
        spec = self.platform.parse(rendered)
        live = live_run_ids(spec.trestle_home) if spec.trestle_home.is_dir() else []
        if live and not drain:
            raise ServiceError(
                f"{len(live)} live run{'s' if len(live) != 1 else ''} on {spec.trestle_home}; "
                "nothing changed. Rerun with --drain to wait for them to end, then upgrade"
            )
        old = before.running_build or before.installed_build
        self._drain_and_unload()
        if install:
            command = self.install_command or default_install_command(spec.binary)
            result = self.run(command)
            if result.returncode != 0:
                self._start()
                tail = (result.stderr or result.stdout).strip().splitlines()
                raise ServiceError(
                    f"install failed ({' '.join(command)}): {tail[-1] if tail else ''}; "
                    "the previous build is running again"
                )
        migrate = [str(spec.binary), "init", "--upgrade", "--no-seed", "--home"]
        result = self.run([*migrate, str(spec.trestle_home)])
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()
            raise ServiceError(f"home migration failed: {tail[-1] if tail else ''}; not started")
        self.unit.write_text(rendered, encoding="utf-8")
        self._start()
        status = self._await_running()
        self.out(
            f"trestle service: upgraded {old or '?'} -> {status.running_build or '?'}; "
            f"{_running_line(status)}"
        )
        return 0

    def _require_installed(self) -> str:
        text = self._unit_text()
        if text is None:
            raise ServiceError("not installed; run `trestle service install`")
        return text

    def _start(self) -> None:
        self.platform.enable()
        if self.platform.probe().loaded:
            self.platform.kick()
        else:
            self.platform.load(self.unit)

    def _drain_and_unload(self) -> None:
        """SIGTERM: the server refuses new runs, finishes the ones it admitted and exits 0, which
        the unit does not restart. Then unload and disable it, so it stays down (across logins
        too) until `start`."""
        probe = self.platform.probe()
        if probe.pid is not None:
            self.platform.terminate()
            while self.platform.probe().pid is not None:
                self.sleep(POLL_S)
        if self.platform.probe().loaded:
            self.platform.unload()
        self.platform.disable()

    def _await_running(self) -> Status:
        """The service is up once its process is running and its server lock (written after the
        port is bound) names that pid."""
        deadline = time.monotonic() + self.start_wait_s
        status = self.status()
        while time.monotonic() < deadline:
            status = self.status()
            if status.running and status.running_build is not None:
                return status
            self.sleep(POLL_S)
        log = status.spec.log if status.spec else "the service log"
        raise ServiceError(f"the service did not come up in {self.start_wait_s:.0f}s; see {log}")


def _server_for(home: Path, pid: int | None) -> dict[str, object] | None:
    if pid is None or not home.is_dir():
        return None
    for row in live_servers(home):
        if row.get("pid") == pid:
            return row
    return None


def _prepare_home(home: Path) -> None:
    check_home(home)
    (home / "plugins").mkdir(parents=True, exist_ok=True)
    ensure_token(home)


def _running_line(status: Status) -> str:
    home = status.spec.trestle_home if status.spec else "?"
    return (
        f"running pid {status.pid} on {status.endpoint} "
        f"(build {status.running_build or '?'}, home {home})"
    )


def _state_line(status: Status) -> str:
    if status.running:
        return _running_line(status)
    return "stopped; start it with `trestle service start`"
