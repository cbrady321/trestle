"""The reference composition root binds the container ports and the ExecutionPort (L.RB-0.3)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, RealizationKind, Repeat
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CancelSignal,
    Confirmation,
    ConfirmationStatus,
    Lineage,
    NodePath,
    SelectorRef,
)

from trestle_env import tree
from trestle_env.plugins import _bind
from trestle_env.plugins._http import HttpReadinessReads
from trestle_env.plugins._tasks import TaskExecution

IMAGE = "postgres@sha256:" + "a" * 64
SUPPORT_IMAGE = "nginx@sha256:" + "b" * 64
ENDPOINT = "unix:///fake/desktop-linux.sock"


class Recorder:
    """An `ExecutionPort` that records every docker argv and answers as a healthy engine."""

    def __init__(self, *, present: bool = True) -> None:
        self.argvs: list[tuple[str, ...]] = []
        self.present = present  # whether a `ps` finds the container

    def policy(self, command: ports.BoundCommand) -> ports.ExecutionPolicy:
        raise NotImplementedError

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        return ports.InRunGroup()

    def run(
        self,
        command: ports.BoundCommand,
        ticket: AttemptTicket,
        cancel: CancelSignal,
        until: datetime,
    ) -> tuple[Confirmation, ports.ExecutionResult | None]:
        self.argvs.append(tuple(command.argv))
        listing = "0123456789ab\n" if self.present or "ps" not in command.argv else ""
        result = ports.ExecutionResult(0, ports.ExecutionClass.PASSED, None, (), None, listing)
        return Confirmation(ConfirmationStatus.APPLIED, None, None), result


def make_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\n", encoding="utf-8")
    docker.chmod(0o755)
    return {
        _bind.DOCKER_PATH_ENV: str(docker),
        _bind.ENDPOINT_ENV: ENDPOINT,
        f"{_bind.IMAGE_ENV_PREFIX}POSTGRES": IMAGE,
        f"{_bind.IMAGE_ENV_PREFIX}HTTP_SUPPORT": SUPPORT_IMAGE,
        **extra,
    }


def ticket(lineage: Lineage) -> AttemptTicket:
    return AttemptTicket(
        lineage=lineage,
        effect=tree.UP,
        facet=EffectFacetClass.CREATE,
        attempt=1,
        repeat=Repeat.SAFE,
        lifetime=Lifetime.RUN,
        release=ports.InRunGroup(),
        remedy=None,
    )


LINEAGE = Lineage("r_test", NodePath((tree.POSTGRES_SERVICE,)))


def test_the_port_map_binds_the_container_ports_and_the_execution_port(tmp_path: Path) -> None:
    recorder = Recorder()
    bound = _bind.reference_ports(make_env(tmp_path), execution=recorder)
    assert set(bound) == {
        ports.ResourceReads,
        ports.ResourceCreate,
        ports.ResourceOwned,
        ports.ResourceSafeStart,
        ports.ExecutionPort,
    }  # no Compose definition configured: no resolver bound
    # the execution port is the toolchain leg's: it runs a task through the runner, or nothing
    assert isinstance(bound[ports.ExecutionPort], TaskExecution)
    # the reads answer the tree's HTTP readiness contracts over the container adapter
    assert isinstance(bound[ports.ResourceReads], HttpReadinessReads)
    assert bound[ports.ResourceCreate] is bound[ports.ResourceOwned]


def test_create_runs_the_declared_definition_from_the_pinned_role_image(tmp_path: Path) -> None:
    recorder = Recorder(present=False)  # creation is idempotent per selector: it must be absent
    bound = _bind.reference_ports(make_env(tmp_path), execution=recorder)
    create = bound[ports.ResourceCreate]
    spec = ports.ResourceSpec(
        tree.POSTGRES_SERVICE, RealizationKind.DOCKER_SERVICE, tree.POSTGRES_SERVICE, None
    )
    create.create(spec, ticket(LINEAGE))  # type: ignore[attr-defined]
    runs = [a for a in recorder.argvs if "run" in a]
    assert len(runs) == 1
    argv = runs[0]
    assert argv[:3] == (os.path.join(str(tmp_path), "docker"), "--host", ENDPOINT)
    assert "--pull" in argv and argv[argv.index("--pull") + 1] == "never"
    assert IMAGE in argv
    assert not any(part in ("-v", "--volume", "--volumes") for part in argv)
    assert "--tmpfs" in argv and _bind.POSTGRES_DATA in argv
    assert f"POSTGRES_PASSWORD={tree.POSTGRES_FIXTURE_PASSWORD}" in argv
    name = argv[argv.index("--name") + 1]
    assert name == "trwr-r_test-postgres"


def test_readiness_check_execs_the_declared_authenticated_call(tmp_path: Path) -> None:
    recorder = Recorder()
    bound = _bind.reference_ports(make_env(tmp_path), execution=recorder)
    reads = bound[ports.ResourceReads]
    target = SelectorRef(LINEAGE, tree.UP, "trwr-r_test-postgres", datetime.now(UTC))
    result = reads.check(tree.POSTGRES_READY, target)  # type: ignore[attr-defined]
    assert result.satisfied
    exec_argv = next(a for a in recorder.argvs if "exec" in a)
    at = exec_argv.index("exec")
    declared = tree.POSTGRES_READINESS
    assert exec_argv[at + 1 : at + 3] == ("-e", f"PGPASSWORD={tree.POSTGRES_FIXTURE_PASSWORD}")
    assert exec_argv[at + 3] == "trwr-r_test-postgres"
    assert exec_argv[at + 4 :] == declared.argv


def test_a_planted_readiness_password_reaches_only_the_exec_environment(tmp_path: Path) -> None:
    recorder = Recorder()
    planted = {"PGPASSWORD": "planted-wrong-password"}
    bound = _bind.reference_ports(
        make_env(tmp_path), execution=recorder, readiness_environment=planted
    )
    target = SelectorRef(LINEAGE, tree.UP, "trwr-r_test-postgres", datetime.now(UTC))
    bound[ports.ResourceReads].check(tree.POSTGRES_READY, target)  # type: ignore[attr-defined]
    exec_argv = next(a for a in recorder.argvs if "exec" in a)
    assert "PGPASSWORD=planted-wrong-password" in exec_argv
    assert not any(tree.POSTGRES_FIXTURE_PASSWORD in part for part in exec_argv)


def test_a_missing_image_pin_or_docker_path_names_what_is_missing(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    del env[f"{_bind.IMAGE_ENV_PREFIX}POSTGRES"]
    with pytest.raises(_bind.BindingError, match="TRESTLE_IMAGE_POSTGRES"):
        _bind.reference_ports(env, execution=Recorder())
    env = make_env(tmp_path)
    env[_bind.DOCKER_PATH_ENV] = "docker"
    with pytest.raises(_bind.BindingError, match="absolute"):
        _bind.reference_ports({**env, "PATH": ""}, execution=Recorder())


def test_the_ports_seam_replaces_the_binding_for_a_proof_harness(tmp_path: Path) -> None:
    marker = {ports.ResourceReads: object()}
    SEAM.calls.append(marker)
    env = {_bind.PORTS_ENV: f"{__name__}:seam_factory"}  # no docker path, no images: not read
    bound = _bind.reference_ports(env)
    assert bound[ports.ResourceReads] is marker[ports.ResourceReads]
    assert isinstance(bound[ports.ExecutionPort], TaskExecution)  # a run of nothing still binds
    with pytest.raises(_bind.BindingError, match="module:callable"):
        _bind.reference_ports({_bind.PORTS_ENV: "no-colon"})


class _Seam:
    def __init__(self) -> None:
        self.calls: list[dict[type, object]] = []


SEAM = _Seam()


def seam_factory(environ: dict[str, str]) -> dict[type, object]:
    assert _bind.PORTS_ENV in environ
    return SEAM.calls[-1]


def test_the_compose_resolver_is_bound_to_the_reference_definition(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    bound = _bind.reference_ports(
        make_env(tmp_path, **{_bind.COMPOSE_ENV: str(compose)}), execution=Recorder()
    )
    assert ports.ComposeResolver in bound
