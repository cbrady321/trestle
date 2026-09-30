"""L.NW-2.6: the real Docker effects (B3-C3..C6, V-10, V-10.4, MC-B-01, X-11).

Every test drives `ContainerPort` over the real `CommandPort` and the absolute-path `fake_docker.py`
shim (the `rig` fixture); the shim's log is the record of every argv docker was given. The same
effects against a real engine are the family suite's `[real]` node (`docker_host`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from trestle.workflow import ports
from trestle.workflow import services as svc
from trestle.workflow.declarations import EffectFacetClass, Lifetime, RealizationKind, Repeat
from trestle.workflow.values import (
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    Lineage,
    NodePath,
)

from trestle_packs.container import engine
from trestle_packs.container.effects import ContainerDefinition, ContainerPort
from trestle_packs.process.command import CommandPort

LINEAGE = Lineage("r_suite_0001", NodePath(("suite",)))
SELECTOR = "trwr-r_suite_0001-suite"
SPEC = ports.ResourceSpec("suite-db", RealizationKind.DOCKER_SERVICE, "suite-entry", None)
RELEASE_TIMEOUT = timedelta(seconds=30)
IMAGE = "alpine:3.20"
DEFINITION = ContainerDefinition(
    IMAGE,
    command=("sleep", "3600"),
    environment={"B": "2", "A": "1"},
    data_paths=("/data", "/var/lib/db"),
    ports=(5432, 8080),
)
FORBIDDEN = {"-v", "--volumes", "-f", "--force", "--volumes-from", "--volume"}
EFFECT_VERBS = {"run", "stop", "rm", "restart", "start"}


def port_for(rig: Any, definition: ContainerDefinition | None = DEFINITION) -> ContainerPort:
    return ContainerPort(rig.docker, {"suite-entry": definition} if definition else {})


def ticket(
    facet: EffectFacetClass = EffectFacetClass.CREATE,
    lifetime: Lifetime = Lifetime.RUN,
    attempt: int = 1,
    lineage: Lineage = LINEAGE,
) -> svc.AttemptTicket:
    return svc.AttemptTicket(
        lineage, "up", facet, attempt, Repeat.SAFE, lifetime, ports.InRunGroup(), None
    )


def call(member: str, args: dict[str, Any], lifetime: Lifetime = Lifetime.RUN) -> ports.EffectCall:
    return ports.EffectCall(member, args, LINEAGE, "up", lifetime, RELEASE_TIMEOUT)


def handle(port: ContainerPort, selector: str = SELECTOR) -> CreatedHandle:
    release = port.release_descriptor(call("create", {"spec": SPEC}))
    return CreatedHandle(LINEAGE, "up", selector, release)


def row(name: str, state: str = "running", **extra: Any) -> dict[str, Any]:
    return {"id": "c" + name[-8:], "names": [name], "image": IMAGE, "state": state, **extra}


def seed(rig: Any, *containers: dict[str, Any]) -> None:
    rig.write(**{**rig.read(), "containers": list(containers)})


def effect_calls(rig: Any) -> list[list[str]]:
    return [c["args"] for c in rig.calls() if c["args"][0] in EFFECT_VERBS]


@pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:b-descriptor-before-effect", "B", "B", "LOGIC", "CI")
def test_create_pull_never_and_selector_name(rig) -> None:
    port = port_for(rig)
    conf = port.create(SPEC, ticket())
    assert conf.status is ConfirmationStatus.APPLIED and conf.identity == SELECTOR
    (run,) = [c for c in rig.calls() if c["args"][0] == "run"]
    assert run["host"] == "unix:///fake/desktop-linux.sock"
    args = run["args"]
    assert args[args.index("--name") + 1] == SELECTOR  # the exact run-scoped name
    assert args[args.index("--pull") + 1] == "never"
    assert "pull" not in [c["args"][0] for c in rig.calls()]  # never a pull verb
    assert args == [
        "run", "-d", "--pull", "never", "--name", SELECTOR,
        "--tmpfs", "/data", "--tmpfs", "/var/lib/db",
        "--publish", "127.0.0.1::5432", "--publish", "127.0.0.1::8080",
        "-e", "A=1", "-e", "B=2",
        IMAGE, "sleep", "3600",
    ]  # fmt: skip
    state = rig.read()["containers"][0]
    assert state["names"] == [SELECTOR] and state["tmpfs"] == ["/data", "/var/lib/db"]
    assert rig.read()["volumes"] == []  # data paths are tmpfs: no volume exists


@pytest.mark.proves(
    "WR-OWN-4", "WR-OWN-4:b-port-stop-never-removes-volume", "B", "B", "LOGIC", "CI"
)
def test_no_volume_flag_in_any_argv(rig) -> None:
    seed(rig, row("suite-db"))
    rig.write(**{**rig.read(), "volumes": [{"name": "keep", "labels": {}}]})
    port = port_for(rig)
    port.create(SPEC, ticket())
    owned = handle(port)
    port.restart(owned, ticket(EffectFacetClass.OWNED))
    port.recreate(owned, ticket(EffectFacetClass.OWNED))
    port.stop(owned, ticket(EffectFacetClass.OWNED))
    port.start(FoundRef("docker_container", "suite-db", datetime.now(UTC)), ticket())
    form = port.release_descriptor(call("create", {"spec": SPEC}))
    argvs = [c["argv"] for c in rig.calls()] + [
        list(form.observe_argv),
        list(form.stop_argv),
        list(form.remove_argv or ()),
    ]
    assert argvs
    for argv in argvs:
        assert not FORBIDDEN & set(argv), argv  # no -v, --volumes or --force in any argv
        assert "pull" not in argv[3:4]
    assert rig.read()["volumes"] == [{"name": "keep", "labels": {}}]  # nothing removed one


@pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:b-descriptor-before-effect", "B", "B", "LOGIC", "CI")
def test_release_descriptor_is_argv_release_abs_docker(rig) -> None:
    port = port_for(rig)
    first = port.release_descriptor(call("create", {"spec": SPEC}))
    assert isinstance(first, ports.ArgvRelease)
    assert first.executable == rig.executable and first.executable.startswith("/")
    assert rig.calls() == []  # derived BEFORE the effect: docker has not been asked anything
    assert port.release_descriptor(call("create", {"spec": SPEC})) == first  # equal across attempts
    port.create(SPEC, ticket())
    assert port.release_descriptor(call("create", {"spec": SPEC})) == first  # and after the effect
    assert first.timeout <= RELEASE_TIMEOUT and first.remove_argv is not None
    host = ["--host", "unix:///fake/desktop-linux.sock"]
    for argv in (first.observe_argv, first.stop_argv, first.remove_argv):
        assert list(argv[:3]) == [rig.executable, *host]  # --host carried in every argv
    assert first.observe_ok_exit == frozenset({0})
    encoded = len(first.executable) + sum(
        len(p) for a in (first.observe_argv, first.stop_argv, first.remove_argv) for p in a
    )
    assert encoded <= 4096  # ARGV_RELEASE_MAX
    long_timeout = port.release_descriptor(
        ports.EffectCall("create", {"spec": SPEC}, LINEAGE, "up", Lifetime.RUN, timedelta(hours=1))
    )
    assert long_timeout.timeout <= timedelta(hours=1)  # never above the declared release_timeout


def test_the_release_descriptor_must_fit_its_bound(rig) -> None:
    deep = Lineage("r_suite_0001", NodePath(("segment-" + "x" * 100,) * 30))
    with pytest.raises(ValueError, match="ARGV_RELEASE_MAX"):
        port_for(rig).release_descriptor(
            ports.EffectCall("create", {"spec": SPEC}, deep, "up", Lifetime.RUN, RELEASE_TIMEOUT)
        )


@pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:b-descriptor-before-effect", "B", "B", "LOGIC", "CI")
def test_observe_argv_exact_name_ps_aq_discriminates(rig) -> None:
    port = port_for(rig)
    form = port.release_descriptor(call("create", {"spec": SPEC}))
    host = ["--host", "unix:///fake/desktop-linux.sock"]
    assert list(form.observe_argv) == [
        rig.executable, *host, "ps", "-aq", "--no-trunc", "--filter", f"name=^/{SELECTOR}$"
    ]  # fmt: skip
    assert "inspect" not in form.observe_argv  # docker inspect exits 1 for absent AND unreachable
    run = lambda argv: engine.DockerCli(  # noqa: E731
        rig.executable, None, CommandPort()
    ).execution.run(  # the sweep runs it as the port would, with nothing but the argv
        _bound(argv), ticket(), engine.NEVER_CANCEL, datetime.now(UTC) + timedelta(seconds=30)
    )
    seed(rig, row(SELECTOR, "exited"), row(SELECTOR + "x"))
    _, present = run(form.observe_argv)
    assert present.exit_status == 0 and present.excerpt.strip() != ""  # a stopped one is listed
    seed(rig)
    _, absent = run(form.observe_argv)
    assert absent.exit_status == 0 and absent.excerpt.strip() == ""  # engine answered: absent
    rig.write(**{**rig.read(), "reachable": False})
    _, down = run(form.observe_argv)
    assert down.exit_status != 0  # outside observe_ok_exit: never read as absent


def _bound(argv: Any) -> ports.BoundCommand:
    return ports.BoundCommand(
        "docker.sweep", tuple(argv), {}, ports.Resolved(argv[0], "", "", ""), False
    )


@pytest.mark.proves(
    "WR-OWN-4", "WR-OWN-4:b-port-stop-never-removes-volume", "B", "B", "LOGIC", "CI"
)
def test_stop_then_remove_argv_never_volumes(rig) -> None:
    port = port_for(rig)
    form = port.release_descriptor(call("create", {"spec": SPEC}))
    host = ["--host", "unix:///fake/desktop-linux.sock"]
    assert list(form.stop_argv) == [rig.executable, *host, "stop", SELECTOR]  # graceful, by name
    assert list(form.remove_argv) == [rig.executable, *host, "rm", SELECTOR]  # no -v, no --force
    port.create(SPEC, ticket())
    rig.write(**{**rig.read(), "volumes": [{"name": "keep", "labels": {}}]})
    conf = port.stop(handle(port), ticket(EffectFacetClass.OWNED))
    assert conf.status is ConfirmationStatus.APPLIED
    verbs = [a[0] for a in effect_calls(rig)]
    assert verbs[-2:] == ["stop", "rm"]  # stop, then remove: a stopped container is not released
    assert rig.read()["containers"] == [] and rig.read()["volumes"][0]["name"] == "keep"
    assert port.observe(SPEC, LINEAGE, "up").selector_present is False


@pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:b-descriptor-before-effect", "B", "B", "LOGIC", "CI")
def test_durable_iff_lifetime_durable(rig) -> None:
    port = port_for(rig)
    durable = port.release_descriptor(call("create", {"spec": SPEC}, Lifetime.DURABLE))
    assert isinstance(durable, ports.Durable) and durable.owner is ports.DurableOwner.ENVIRONMENT
    run = port.release_descriptor(call("create", {"spec": SPEC}, Lifetime.RUN))
    assert not isinstance(run, ports.Durable) and isinstance(run, ports.ArgvRelease)
    found = FoundRef("docker_container", "suite-db", datetime.now(UTC))
    engine_ref = FoundRef("docker_engine", "docker", datetime.now(UTC))
    for member, target, owner in (
        ("start", found, ports.DurableOwner.ENVIRONMENT),
        ("start", engine_ref, ports.DurableOwner.HOST),
    ):
        got = port.release_descriptor(call(member, {"target": target}))
        assert got == ports.Durable(owner)  # a SafeStartFacet member is always Durable
    owned = handle(port)
    for member in ("restart", "recreate", "stop"):
        assert port.release_descriptor(call(member, {"target": owned})) == owned.release
    provisioned = ports.ResourceSpec("x", RealizationKind.PROVISIONED, "e", None)
    with pytest.raises(ValueError, match="DOCKER_SERVICE"):
        port.release_descriptor(call("create", {"spec": provisioned}))
    assert rig.calls() == []  # pure: none of this touched docker


@pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:b-descriptor-before-effect", "B", "B", "LOGIC", "CI")
def test_occupied_selector_create_applied_same_identity(rig) -> None:
    port = port_for(rig)
    first = port.create(SPEC, ticket(attempt=1))  # attempt 1 landed; its confirmation was lost
    again = port.create(SPEC, ticket(attempt=2))
    assert first.status is ConfirmationStatus.APPLIED is again.status
    assert first.identity == again.identity == SELECTOR
    runs = [a for a in effect_calls(rig) if a[0] == "run"]
    assert len(runs) == 1  # no second create argv
    assert [c["names"] for c in rig.read()["containers"]] == [[SELECTOR]]  # one instance
    seed(rig, row(SELECTOR, "exited"))  # a stopped instance of the same selector also converges
    stopped = port_for(rig).create(SPEC, ticket(attempt=3))
    assert stopped.status is ConfirmationStatus.APPLIED and stopped.identity == SELECTOR


def test_all_invocations_via_execution_port(rig) -> None:
    seen: list[tuple[str, ...]] = []

    class Recording:
        def run(self, command, execution_ticket, cancel, until):  # type: ignore[no-untyped-def]
            seen.append(tuple(command.argv))
            assert command.argv[0] == command.resolved.executable == rig.executable
            assert command.environment == {}  # built from empty (B2-C9)
            assert isinstance(execution_ticket, svc.AttemptTicket)
            return rig.docker.execution.run(command, execution_ticket, cancel, until)

    docker = engine.DockerCli(rig.executable, "unix:///e.sock", Recording())
    port = ContainerPort(docker, {"suite-entry": DEFINITION})
    conf = port.create(SPEC, ticket())
    owned = handle(port)
    port.restart(owned, ticket(EffectFacetClass.OWNED))
    port.recreate(owned, ticket(EffectFacetClass.OWNED))
    port.stop(owned, ticket(EffectFacetClass.OWNED))
    assert conf.status is ConfirmationStatus.APPLIED and len(seen) >= 8
    assert all(argv[1:3] == ("--host", "unix:///e.sock") for argv in seen)
    assert {argv[3] for argv in seen} <= {"ps", "run", "stop", "rm", "restart"}


def test_nothing_changed_is_the_only_not_applied(rig, tmp_path) -> None:
    rig.write(**{**rig.read(), "reachable": False})
    down = port_for(rig).create(SPEC, ticket())
    assert down.status is ConfirmationStatus.NOT_APPLIED
    assert down.code == engine.DOCKER_ENGINE_UNREACHABLE and down.identity is None
    assert (
        effect_calls(rig) == []
    )  # not even a run was attempted: it could not have changed anything
    gone = engine.DockerCli(str(tmp_path / "no-docker"), None, CommandPort())
    missing = ContainerPort(gone, {"suite-entry": DEFINITION}).create(SPEC, ticket())
    assert missing.status is ConfirmationStatus.NOT_APPLIED
    assert missing.code == engine.DOCKER_CLI_MISSING
    rig.write(**{**rig.read(), "reachable": True, "images": []})  # `run` fails: no such image
    refused = port_for(rig).create(SPEC, ticket())
    assert refused.status is ConfirmationStatus.NOT_APPLIED and refused.code is None
    assert rig.read()["containers"] == []  # observed absent after the call: nothing changed


def test_a_create_cancelled_before_it_starts_changed_nothing(rig) -> None:
    class Cancelled:
        requested = True

        def cause(self) -> Any:
            from trestle.workflow.values import StopCause

            return StopCause.CANCEL

        def wait(self, timeout: Any) -> bool:
            return True

    docker = engine.DockerCli(rig.executable, None, rig.docker.execution, Cancelled())
    conf = ContainerPort(docker, {"suite-entry": DEFINITION}).create(SPEC, ticket())
    # the very first read was ended by the port: nothing was changed, and the adapter says only that
    assert conf.status is ConfirmationStatus.NOT_APPLIED and conf.code == "execution.cancelled"
    assert effect_calls(rig) == []


def test_a_run_interrupted_after_it_started_is_unknown(rig) -> None:
    class InterruptsRun:
        def run(self, command, execution_ticket, cancel, until):  # type: ignore[no-untyped-def]
            if command.argv[3] == "run":  # the port ended the process: it may have created it
                result = ports.ExecutionResult(
                    -15, ports.ExecutionClass.INTERRUPTED, None, (), "execution.cancelled", ""
                )
                return Confirmation(ConfirmationStatus.APPLIED, None, None), result
            return rig.docker.execution.run(command, execution_ticket, cancel, until)

    docker = engine.DockerCli(rig.executable, "unix:///e.sock", InterruptsRun())
    conf = ContainerPort(docker, {"suite-entry": DEFINITION}).create(SPEC, ticket())
    assert conf.status is ConfirmationStatus.UNKNOWN and conf.identity == SELECTOR  # not "nothing"


def test_only_a_run_scoped_container_is_ever_stopped_restarted_or_recreated(rig) -> None:
    seed(rig, row("suite-db"))
    port = port_for(rig)
    found = CreatedHandle(LINEAGE, "up", "suite-db", ports.InRunGroup())
    for member in ("stop", "restart", "recreate"):
        with pytest.raises(ValueError, match="run-scoped"):
            getattr(port, member)(found, ticket(EffectFacetClass.OWNED))
    assert rig.calls() == []  # a found instance is never touched: docker was not asked
    assert [c["names"] for c in rig.read()["containers"]] == [["suite-db"]]


def test_repair_members_change_only_an_owned_instance(rig) -> None:
    port = port_for(rig)
    absent = handle(port)
    assert port.restart(absent, ticket(EffectFacetClass.OWNED)).status is (
        ConfirmationStatus.NOT_APPLIED
    )
    assert port.stop(absent, ticket(EffectFacetClass.OWNED)).status is ConfirmationStatus.APPLIED
    port.create(SPEC, ticket())
    assert port.restart(handle(port), ticket(EffectFacetClass.OWNED)).identity == SELECTOR
    stranger = ContainerPort(rig.docker, {})  # never made the instance: cannot make it again
    assert stranger.recreate(handle(port), ticket(EffectFacetClass.OWNED)).status is (
        ConfirmationStatus.NOT_APPLIED
    )
    recreated = port.recreate(handle(port), ticket(EffectFacetClass.OWNED))
    assert recreated.status is ConfirmationStatus.APPLIED and recreated.identity == SELECTOR
    assert [c["names"] for c in rig.read()["containers"]] == [[SELECTOR]]


def test_start_moves_stopped_to_running_and_never_starts_an_engine(rig) -> None:
    seed(rig, row("suite-db", "exited"))
    port = port_for(rig)
    target = FoundRef("docker_container", "suite-db", datetime.now(UTC))
    assert port.start(target, ticket(EffectFacetClass.SAFE_START)).status is (
        ConfirmationStatus.APPLIED
    )
    assert rig.read()["containers"][0]["state"] == "running"
    assert [a[0] for a in effect_calls(rig)] == ["start"]  # nothing else: no restart, no recreate
    engine_ref = FoundRef("docker_engine", "docker", datetime.now(UTC))
    refused = port.start(engine_ref, ticket(EffectFacetClass.SAFE_START))
    assert refused.status is ConfirmationStatus.NOT_APPLIED  # the adapter never starts an engine
    missing = FoundRef("docker_container", "ghost", datetime.now(UTC))
    assert port.start(missing, ticket(EffectFacetClass.SAFE_START)).status is (
        ConfirmationStatus.NOT_APPLIED
    )


def test_create_without_a_declared_definition_raises_before_any_call(rig) -> None:
    with pytest.raises(ValueError, match="no container definition"):
        port_for(rig, None).create(SPEC, ticket())
    assert rig.calls() == []


def test_bind_returns_one_composite_for_the_four_resource_protocols(rig) -> None:
    from pathlib import Path

    from trestle_packs.container import bind

    bound = bind(
        Path(rig.executable),
        "unix:///fake/desktop-linux.sock",
        CommandPort(),
        definitions={"suite-entry": DEFINITION},
    )
    assert bound.compose is None  # the compose resolver joins with L.NW-2.7
    mapping = bound.as_map()
    assert set(mapping) == {
        ports.ResourceReads,
        ports.ResourceCreate,
        ports.ResourceOwned,
        ports.ResourceSafeStart,
    }
    assert len({id(impl) for impl in mapping.values()}) == 1  # one composite adapter
    assert bound.containers.create(SPEC, ticket()).identity == SELECTOR
    with pytest.raises(ValueError, match="absolute"):
        bind("docker", None, CommandPort())  # a bare name is never resolved through PATH
