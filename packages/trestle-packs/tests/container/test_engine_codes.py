"""L.NW-2.3: the Docker CLI locator and the engine-reachability read (B3-C1, B3-E1, V-3.8).

A missing CLI and an unreachable engine are two values with two stable codes, returned and never
raised; the locator never searches `PATH`. The fake binding runs the absolute-path `fake_docker.py`
shim through the real `CommandPort` (the injected `ExecutionPort`); the real binding runs the
operator's docker CLI against an endpoint that does not exist (`docker_host`: the host-docker gate
only, and it touches no engine).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import fake_docker

from trestle_packs.container import engine
from trestle_packs.process.command import CommandPort


@pytest.fixture
def shim(tmp_path: Path):
    state = tmp_path / "state.json"
    path = fake_docker.install_shim(tmp_path, state, tmp_path / "log.jsonl")

    def write(**kw: object) -> None:
        fake_docker.write_state(state, **kw)

    write(reachable=True, server_version="29.8.0")
    return path, write, tmp_path / "log.jsonl"


BINDINGS = [
    pytest.param(
        "fake",
        id="fake",
        marks=[
            pytest.mark.proves("WR-VERIFY-3", "WR-VERIFY-3:adapter-codes", "B", "B", "LOGIC", "CI"),
            pytest.mark.stub_proven("WR-VERIFY-3:adapter-codes@host@stub-twin"),
        ],
    ),
    pytest.param(
        "real",
        id="real-unreachable-endpoint",
        marks=[
            pytest.mark.docker_host,
            pytest.mark.proves(
                "WR-VERIFY-3", "WR-VERIFY-3:adapter-codes@host", "B", "B", "DOCKER", "HOST"
            ),
        ],
    ),
]


@pytest.mark.parametrize("binding", BINDINGS)
def test_cli_missing_vs_engine_unreachable_distinct_codes(binding: str, tmp_path: Path) -> None:
    if binding == "fake":
        state = tmp_path / "state.json"
        log = tmp_path / "log.jsonl"
        cli = fake_docker.install_shim(tmp_path, state, log)
        fake_docker.write_state(state, reachable=False)
        endpoint = "unix:///fake/desktop-linux.sock"
    else:
        # the test names the operator's path; the adapter itself never searches PATH. The
        # endpoint is a socket that does not exist: the real CLI reaches no engine (none touched).
        found = shutil.which("docker")
        assert found is not None, "the host-docker gate runs with a docker CLI (preflight)"
        cli, endpoint = found, f"unix://{tmp_path}/absent.sock"
    port = CommandPort()
    missing = engine.locate_cli(str(Path(cli).parent / "no-such-docker"))
    assert isinstance(missing, engine.CliMissing)
    assert missing.code == engine.DOCKER_CLI_MISSING
    located = engine.locate_cli(cli)
    assert isinstance(located, Path) and located.is_absolute()
    unreachable = engine.engine_state(located, endpoint, port)
    assert isinstance(unreachable, engine.Unreachable)
    assert unreachable.code == engine.DOCKER_ENGINE_UNREACHABLE
    assert missing.code != unreachable.code
    assert not isinstance(missing, engine.Reachable)  # never "available"
    assert not isinstance(unreachable, engine.Reachable)
    if binding == "fake":
        fake_docker.write_state(tmp_path / "state.json", reachable=True, server_version="29.8.0")
        assert engine.engine_state(located, endpoint, port) == engine.Reachable("29.8.0")
        calls = fake_docker.read_log(log)
        assert calls and all(c["host"] == endpoint for c in calls)
        assert all(c["stdin"] in ("eof", "closed") for c in calls)  # stdin closed (WR-CANCEL-5)


def test_locate_cli_never_searches_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decoy = tmp_path / "bin"
    decoy.mkdir()
    (decoy / "docker").write_text("#!/bin/sh\nexit 0\n")
    (decoy / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{decoy}{os.pathsep}{os.environ.get('PATH', '')}")
    for bare in ("docker", "./docker", "bin/docker", "", None):
        missing = engine.locate_cli(bare)
        assert isinstance(missing, engine.CliMissing), bare
        assert missing.code == engine.DOCKER_CLI_MISSING
    monkeypatch.chdir(decoy)
    assert isinstance(engine.locate_cli("docker"), engine.CliMissing)  # never the working directory
    assert engine.locate_cli(str(decoy / "docker")) == decoy / "docker"


def test_locate_cli_refuses_a_directory_and_a_non_executable(tmp_path: Path) -> None:
    (tmp_path / "docker-dir").mkdir()
    plain = tmp_path / "docker-plain"
    plain.write_text("x")
    plain.chmod(0o644)
    for candidate in (tmp_path / "docker-dir", plain):
        found = engine.locate_cli(str(candidate))
        assert isinstance(found, engine.CliMissing) and found.code == engine.DOCKER_CLI_MISSING


def test_an_unstartable_executable_is_cli_missing_not_unreachable(tmp_path: Path) -> None:
    ghost = tmp_path / "docker"  # located a moment ago, gone when the port starts it
    state = engine.engine_state(ghost, "unix:///x.sock", CommandPort())
    assert isinstance(state, engine.CliMissing) and state.code == engine.DOCKER_CLI_MISSING


def test_engine_state_runs_docker_through_the_injected_port(shim) -> None:
    path, write, log = shim
    calls: list[tuple[str, ...]] = []

    class Recording(CommandPort):
        def run(self, command, ticket, cancel, until):  # type: ignore[no-untyped-def]
            calls.append(tuple(command.argv))
            assert command.environment == {}  # built from empty (B2-C9)
            assert command.argv[0] == command.resolved.executable == path
            return super().run(command, ticket, cancel, until)

    endpoint = "unix:///fake/desktop-linux.sock"
    assert engine.engine_state(path, endpoint, Recording()) == engine.Reachable("29.8.0")
    assert calls == [(path, "--host", endpoint, "info", "--format", "{{.ServerVersion}}")]
    assert engine.engine_state(path, None, Recording()) == engine.Reachable("29.8.0")
    assert calls[-1] == (path, "info", "--format", "{{.ServerVersion}}")  # no endpoint, no flag


def test_a_silent_engine_is_unreachable_not_reachable(shim) -> None:
    path, write, _ = shim
    write(reachable=True, server_version="")
    state = engine.engine_state(path, None, CommandPort())
    assert isinstance(state, engine.Unreachable)  # exit 0 but no version: it did not answer
