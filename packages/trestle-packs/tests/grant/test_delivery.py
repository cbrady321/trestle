"""L.RB-9.2: the credential channel is a mounted refreshable FILE, never an environment variable
(WR-ENV-13), and the consumer probes are what the suite says they are (B3-C9, B3-C11).

The family suite (`test_conformance.py::test_grant_delivery_suite`) proves the port; these tests
prove the properties that are about the channel itself and the two probes' commands. AWS is DEMO
ONLY (D-9): the issuer is the stub on loopback, the credential a stand-in token.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conformance import container_cases
from tests.proof import tolerances
from tests.proof.host.docker_gate import fake_docker
from tests.proof.suites.ports import families
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, RealizationKind
from trestle.workflow.values import Confirmation, ConfirmationStatus, CreatedHandle

from grant.conftest import load_stub
from trestle_packs.container import ContainerDefinition, bind
from trestle_packs.container.effects import BindMount
from trestle_packs.grant import (
    CHANNEL_ENV,
    CHANNEL_FILE,
    CHANNEL_MOUNT,
    ArgvRunner,
    ChannelDelivery,
    ContainerExecProbe,
    LocalAppProbe,
    channel_directory,
    channel_env,
    channel_mount,
    provision_channel,
    write_channel,
)
from trestle_packs.grant.consumer_probe import PROBE_SCRIPT
from trestle_packs.process.command import CommandPort

SELECTOR = "trwr-r_suite_0001-consumer"


@pytest.fixture
def issuer() -> Any:
    with load_stub("stub_issuer").StubIssuer() as running:
        yield running


def owned() -> CreatedHandle:
    lineage = container_cases.lineage("consumer")
    return CreatedHandle(lineage, "up", SELECTOR, ports.InRunGroup())


def owned_ticket() -> Any:
    return families.ticket("deliver", EffectFacetClass.OWNED)


@pytest.mark.proves("WR-ENV-13", "WR-ENV-13:channel-not-env-var", "B", "B", "LOGIC", "CI")
def test_channel_is_refreshable_file_not_env_var(issuer: Any, tmp_path: Path) -> None:
    """The channel is one file in a mounted directory that `deliver` rewrites in place (same
    directory, atomic rename, never a partial file), and the container that mounts it is created
    with the mount and with no credential in its environment."""
    directory = provision_channel(tmp_path, SELECTOR)
    write_channel(directory, issuer.state.token())
    directory_id = directory.stat().st_ino
    delivery = ChannelDelivery(issuer.url, tmp_path)

    stop = threading.Event()
    partial: list[str] = []

    def read_all_the_time() -> None:  # the consumer reading its file while it is refreshed
        while not stop.is_set():
            text = (directory / CHANNEL_FILE).read_text(encoding="utf-8")
            if text.count(":") != 2 or not text.endswith(issuer.state.nonce):
                partial.append(text)

    reader = threading.Thread(target=read_all_the_time, daemon=True)
    reader.start()
    try:
        for _ in range(25):
            issuer.state.advance()
            done = delivery.deliver(owned(), owned_ticket())
            assert done == Confirmation(ConfirmationStatus.APPLIED, None, SELECTOR)
    finally:
        stop.set()
        reader.join()
    assert partial == []  # a reader sees the old file or the new one, never half of it
    assert sorted(p.name for p in directory.iterdir()) == [CHANNEL_FILE]  # no scratch left behind
    assert directory.stat().st_ino == directory_id  # the same directory, mounted all along
    assert (directory / CHANNEL_FILE).read_text(encoding="utf-8") == issuer.state.token()
    assert stat.S_IMODE((directory / CHANNEL_FILE).stat().st_mode) == 0o644


def shim_engine(tmp_path: Path) -> tuple[str, Path]:
    state, log = tmp_path / "state.json", tmp_path / "log.jsonl"
    cli = fake_docker.install_shim(tmp_path, state, log)
    fake_docker.write_state(
        state,
        reachable=True,
        server_version="29.8.0",
        containers=[],
        images=[{"id": "sha256:aa", "repository": "alpine", "tag": "3.20", "repo_digests": []}],
        volumes=[],
        networks=[],
    )
    return cli, log


@pytest.mark.proves("WR-ENV-13", "WR-ENV-13:channel-not-env-var", "B", "B", "LOGIC", "CI")
def test_a_consumer_container_is_created_with_the_channel_mounted_and_no_credential_variable(
    issuer: Any, tmp_path: Path
) -> None:
    source, target = channel_mount(tmp_path / "channels", SELECTOR)
    assert target == CHANNEL_MOUNT
    provision_channel(tmp_path / "channels", SELECTOR)
    write_channel(channel_directory(tmp_path / "channels", SELECTOR), issuer.state.token())
    cli, log = shim_engine(tmp_path)
    definitions = {
        "consumer-entry": ContainerDefinition(
            "alpine:3.20", ("sh", "-c", "sleep 1"), mounts=(BindMount(source, target),)
        )
    }
    containers = bind(
        cli, "unix:///fake/desktop-linux.sock", CommandPort(), definitions=definitions
    )
    spec = ports.ResourceSpec("consumer", RealizationKind.DOCKER_SERVICE, "consumer-entry", None)
    lineage = container_cases.lineage("consumer")
    made = containers.containers.create(
        spec, container_cases.ticket_for(lineage, EffectFacetClass.CREATE)
    )
    assert made.identity == SELECTOR
    run = next(c["args"] for c in fake_docker.read_log(log) if c["args"][:1] == ["run"])
    assert f"type=bind,source={source},target={CHANNEL_MOUNT},readonly" in run
    assert "--mount" in run and "-e" not in run and "--env" not in run  # nothing in the environment
    assert issuer.state.nonce not in " ".join(run)  # and no token on the command line either


def test_a_bind_mount_needs_an_absolute_source_and_no_comma(tmp_path: Path) -> None:
    cli, _ = shim_engine(tmp_path)
    spec = ports.ResourceSpec("consumer", RealizationKind.DOCKER_SERVICE, "consumer-entry", None)
    lineage = container_cases.lineage("consumer")
    for bad in (
        BindMount("relative/dir", "/mnt"),
        BindMount("/a,b", "/mnt"),
        BindMount("/a", "/m,n"),
    ):
        definitions = {"consumer-entry": ContainerDefinition("alpine:3.20", mounts=(bad,))}
        port = bind(cli, None, CommandPort(), definitions=definitions).containers
        with pytest.raises(ValueError, match="bind mount"):
            port.create(spec, container_cases.ticket_for(lineage, EffectFacetClass.CREATE))


def test_a_channel_selector_cannot_leave_the_channels_root(tmp_path: Path) -> None:
    for bad in ("../escape", "trwr-x/../../y", "found-container", "trwr-", "/etc"):
        with pytest.raises(ValueError, match="run-scoped selector"):
            channel_directory(tmp_path, bad)
    for bad in ("proc-", "proc-0123456789abcdef0", "proc-0123456789ABCDEF", "proc-../../etc"):
        with pytest.raises(ValueError, match="run-scoped selector"):
            channel_directory(tmp_path, bad)


PROC_SELECTOR = "proc-0123456789abcdef"  # the shape of `process.local.run_scoped_selector`


def test_an_owned_local_process_has_a_channel_named_by_its_selector(tmp_path: Path) -> None:
    """A local process's channel is the directory its `proc-` selector names; it is told where
    through `TRESTLE_CHANNEL_DIR` (a directory, never a credential), and nothing mounts it."""
    assert channel_directory(tmp_path, PROC_SELECTOR) == tmp_path / PROC_SELECTOR
    assert channel_env(tmp_path, PROC_SELECTOR) == {CHANNEL_ENV: str(tmp_path / PROC_SELECTOR)}
    with pytest.raises(ValueError, match="container selector"):
        channel_mount(tmp_path, PROC_SELECTOR)
    with pytest.raises(ValueError, match="local process selector"):
        channel_env(tmp_path, SELECTOR)


def test_a_delivery_refreshes_an_owned_local_process_channel_in_place(
    issuer: Any, tmp_path: Path
) -> None:
    directory = provision_channel(tmp_path, PROC_SELECTOR)
    write_channel(directory, issuer.state.token())
    issuer.state.advance()
    lineage = container_cases.lineage("consumer")
    handle = CreatedHandle(lineage, "up", PROC_SELECTOR, ports.InRunGroup())
    done = ChannelDelivery(issuer.url, tmp_path).deliver(handle, owned_ticket())
    assert (done.status, done.identity) == (ConfirmationStatus.APPLIED, PROC_SELECTOR)
    assert issuer.state.current() in (directory / CHANNEL_FILE).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------- the probes


class Scripted:
    """An `ExecutionPort` whose one result the test scripts, recording every command it is given."""

    def __init__(self, exit_status: int | None, output: str = "", started: bool = True) -> None:
        self.exit_status, self.output, self.started = exit_status, output, started
        self.commands: list[Any] = []

    def run(self, command: Any, ticket: Any, cancel: Any, until: datetime) -> Any:
        self.commands.append(command)
        if not self.started:
            return Confirmation(
                ConfirmationStatus.NOT_APPLIED, "adapter.docker_cli_missing", None
            ), None
        klass = (
            ports.ExecutionClass.INTERRUPTED
            if self.exit_status is None
            else (
                ports.ExecutionClass.PASSED
                if self.exit_status == 0
                else ports.ExecutionClass.FAILED
            )
        )
        result = ports.ExecutionResult(
            -15 if self.exit_status is None else self.exit_status,
            klass,
            None,
            (),
            "execution.deadline_exceeded" if self.exit_status is None else None,
            self.output,
        )
        return Confirmation(ConfirmationStatus.APPLIED, None, None), result


DOCKER = "/usr/local/bin/docker"
ENDPOINT = "unix:///fake/desktop-linux.sock"
IN_CONTAINER = "http://host.docker.internal:4711"


def exec_probe(execution: Scripted) -> ContainerExecProbe:
    return ContainerExecProbe(DOCKER, ENDPOINT, ArgvRunner(execution), IN_CONTAINER)


def test_the_container_probe_execs_into_the_selector_with_no_token_on_the_command_line() -> None:
    execution = Scripted(0, "authenticated=1 generation=gen-07919\n")
    reading = exec_probe(execution).read(SELECTOR)
    assert (reading.authenticated, reading.generation_seen) == (True, "gen-07919")
    (command,) = execution.commands
    assert command.argv[:5] == (DOCKER, "--host", ENDPOINT, "exec", SELECTOR)
    assert command.argv[5:8] == ("sh", "-c", PROBE_SCRIPT)
    assert command.argv[8:] == ("probe", IN_CONTAINER, f"{CHANNEL_MOUNT}/{CHANNEL_FILE}")
    assert dict(command.environment) == {}  # an environment built from empty
    assert command.resolved.executable == DOCKER and command.reports_tests is False


@pytest.mark.parametrize(
    ("execution", "expected"),
    [
        (Scripted(0, "authenticated=0 generation=gen-15838\n"), (False, "gen-15838")),
        (Scripted(0, "noise\nauthenticated=1 generation=g\n"), (True, "g")),
        (Scripted(0, "authenticated=1 generation=\n"), (True, None)),
        (Scripted(3, ""), (False, None)),  # the container is there, its channel file is not
        (Scripted(1, "Error: No such container"), None),
        (Scripted(0, "garbage"), None),
        (Scripted(None, "", started=True), None),  # ended by the port
        (Scripted(0, "", started=False), None),  # docker could not be started
    ],
)
def test_the_container_probe_reads_only_what_the_script_prints(
    execution: Scripted, expected: tuple[bool, str | None] | None
) -> None:
    reading = exec_probe(execution).read(SELECTOR)
    assert (
        None if reading is None else (reading.authenticated, reading.generation_seen)
    ) == expected


def test_the_probe_script_runs_in_a_posix_shell_and_never_prints_the_token(
    issuer: Any, tmp_path: Path
) -> None:
    """The exact script the container runs, in `sh`, with a `wget` that speaks busybox's flags."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wget = bin_dir / "wget"
    wget.write_text(
        f"#!{sys.executable}\n"
        "import sys, urllib.request, urllib.error\n"
        "args = sys.argv[1:]\n"
        "header = args[args.index('--header') + 1]\n"
        "auth = header.split(': ', 1)[1]\n"
        "req = urllib.request.Request(args[-1], headers={'Authorization': auth})\n"
        "try:\n"
        "    urllib.request.urlopen(req, timeout=10)\n"
        "except urllib.error.URLError:\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    wget.chmod(0o755)
    credentials = tmp_path / CHANNEL_FILE

    def probe() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - /bin/sh with the exact probe script
            ["/bin/sh", "-c", PROBE_SCRIPT, "probe", issuer.url, str(credentials)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
            timeout=tolerances.JOIN_WAIT_S,
            check=False,
        )

    first = issuer.state.current()
    credentials.write_text(issuer.state.token(), encoding="utf-8")
    done = probe()
    assert (done.returncode, done.stdout) == (0, f"authenticated=1 generation={first}\n")
    credentials.write_text(issuer.state.token("gen-never-issued"), encoding="utf-8")
    assert probe().stdout == "authenticated=0 generation=gen-never-issued\n"
    credentials.unlink()
    assert probe().returncode == 3  # no channel file
    for text in (done.stdout, done.stderr):
        assert all(secret not in text for secret in issuer.state.secret_values())


def test_the_probe_refuses_a_docker_that_is_not_absolute_and_an_issuer_that_is_not_the_demo() -> (
    None
):
    runner = ArgvRunner(Scripted(0))
    with pytest.raises(ValueError, match="absolute"):
        ContainerExecProbe("docker", None, runner, IN_CONTAINER)
    for url in ("http://example.com", "https://host.docker.internal", "http://10.1.2.3:80"):
        with pytest.raises(ValueError, match="loopback or host.docker.internal"):
            ContainerExecProbe(DOCKER, None, runner, url)
        with pytest.raises(ValueError, match="loopback or host.docker.internal"):
            LocalAppProbe(sys.executable, "/stub", url, lambda s: None, runner)
    with pytest.raises(ValueError, match="absolute"):
        runner.run(["docker", "ps"])


def test_the_local_app_probe_has_no_reading_for_a_consumer_without_a_credentials_file(
    tmp_path: Path,
) -> None:
    execution = Scripted(0, '{"Generation": "g"}')
    probe = LocalAppProbe(
        sys.executable,
        "/stub",
        "http://127.0.0.1:1",
        lambda s: tmp_path / "absent",
        ArgvRunner(execution),
    )
    assert probe.read("anyone") is None and execution.commands == []


def test_the_local_app_probe_runs_the_demo_client_with_the_file_in_its_environment(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text("demo-token:g:n", encoding="utf-8")
    execution = Scripted(254, '{"Error": "InvalidClientTokenId", "Generation": "g"}')
    probe = LocalAppProbe(
        sys.executable,
        "/stub_cloud.py",
        "http://127.0.0.1:1",
        lambda s: credentials,
        ArgvRunner(execution),
    )
    reading = probe.read("app")
    assert (reading.authenticated, reading.generation_seen) == (False, "g")
    (command,) = execution.commands
    assert command.argv == (sys.executable, "/stub_cloud.py", "sts", "get-caller-identity")
    assert dict(command.environment) == {
        "STUB_CLOUD_CREDENTIALS_FILE": str(credentials),
        "STUB_CLOUD_ENDPOINT_URL": "http://127.0.0.1:1",
    }


def test_an_unauthenticated_local_app_without_a_generation_still_has_a_reading(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text("x", encoding="utf-8")
    execution = Scripted(255, '{"Error": "EndpointConnectionError", "Generation": null}')
    probe = LocalAppProbe(
        sys.executable, "/s", "http://127.0.0.1:1", lambda s: credentials, ArgvRunner(execution)
    )
    reading = probe.read("app")
    assert (reading.authenticated, reading.generation_seen) == (False, None)


def test_the_delivery_needs_the_channel_to_exist_and_never_makes_it(
    issuer: Any, tmp_path: Path
) -> None:
    delivery = ChannelDelivery(issuer.url, tmp_path)
    done = delivery.deliver(owned(), owned_ticket())
    assert (done.status, done.code, done.identity) == (ConfirmationStatus.NOT_APPLIED, None, None)
    assert list(tmp_path.iterdir()) == []


def test_a_delivery_into_an_unwritable_channel_is_not_applied(issuer: Any, tmp_path: Path) -> None:
    directory = provision_channel(tmp_path, SELECTOR)
    write_channel(directory, issuer.state.token())
    before = (directory / CHANNEL_FILE).read_text(encoding="utf-8")
    issuer.state.advance()
    directory.chmod(0o555)
    try:
        if os.access(directory, os.W_OK):
            pytest.skip("this account can write into a read-only directory")
        done = ChannelDelivery(issuer.url, tmp_path).deliver(owned(), owned_ticket())
    finally:
        directory.chmod(0o755)
    assert done.status is ConfirmationStatus.NOT_APPLIED  # the rename never happened
    assert (directory / CHANNEL_FILE).read_text(encoding="utf-8") == before
