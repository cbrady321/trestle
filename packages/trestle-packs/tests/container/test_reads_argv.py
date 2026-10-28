"""L.NW-2.5: the real Docker read facets (B3-C1, B3-C2, V-3.8, V-5.1, MC-B-01).

Every test drives `ContainerReads` over the real `CommandPort` and the absolute-path
`fake_docker.py` shim (the `rig` fixture): the shim's log is the record of what docker was asked.
The real-engine binding of the same reads is the family suite's `[real]` node (`docker_host`).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.proof.suites.ports import core
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind, Vantage
from trestle.workflow.values import CreatedHandle, FoundRef, Lineage, NodePath, SelectorRef

from trestle_packs.container import engine, reads
from trestle_packs.container.reads import ContainerReads, ExecCheck

LINEAGE = Lineage("r_suite_0001", NodePath(("suite",)))
SELECTOR = "trwr-r_suite_0001-suite"
SPEC = ports.ResourceSpec("suite-db", RealizationKind.DOCKER_SERVICE, "suite-entry", None)
READ_VERBS = {"ps", "port", "exec"}


def row(
    name: str,
    *,
    state: str = "running",
    ports: list[dict[str, int]] | None = None,
    labels: dict[str, str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": "c" + name.replace("-", "")[-8:],
        "names": [name],
        "image": "alpine",
        "state": state,
        "labels": labels or {},
        "ports": ports or [],
        **extra,
    }


@pytest.mark.proves(
    "WR-OWN-7", "WR-OWN-7:b-identity-not-port-occupancy-adapter", "B", "B", "LOGIC", "CI"
)
def test_observe_uses_exact_selector_no_label_ownership(rig) -> None:
    reader = ContainerReads(rig.docker)
    rig.write(
        **{
            **rig.read(),
            "containers": [
                # look-alikes: a prefix, a longer name, and a label that claims ownership
                row("trwr-r_suite_0001-sui", labels={"trestle.owner": "r_suite_0001"}),
                row(SELECTOR + "x", labels={"trestle.owner": "r_suite_0001"}),
                row("suite-db", ports=[{"private": 5432, "host": 55432}]),  # a found instance
            ],
        }
    )
    seen = reader.observe(SPEC, LINEAGE, "up")
    assert seen.selector_present is False and seen.selector_ref is None
    assert seen.identity_proven is False  # a name, a label or a port occupied proves nothing
    assert [f.selector for f in seen.found] == ["suite-db"]  # the only other instance, in `found`
    rig.write(**{**rig.read(), "containers": rig.read()["containers"] + [row(SELECTOR)]})
    seen = reader.observe(SPEC, LINEAGE, "up")
    assert seen.selector_present is True and seen.selector_ref.selector == SELECTOR
    assert seen.identity_proven is True and seen.code is None
    calls = rig.calls()
    assert {c["args"][0] for c in calls} == {"ps"}  # only a read verb
    filters = {a for c in calls for a in c["args"] if a.startswith("name=")}
    assert filters == {f"name=^/{SELECTOR}$", "name=^/suite-db$"}  # the exact MC-B-01 filters
    assert not [a for c in calls for a in c["args"] if "label" in a]  # never label ownership
    assert all(c["host"] == "unix:///fake/desktop-linux.sock" for c in calls)  # --host carried


def test_observe_of_a_node_with_no_create_effect_reads_no_selector(rig) -> None:
    rig.write(**{**rig.read(), "containers": [row(SELECTOR)]})
    seen = ContainerReads(rig.docker).observe(SPEC, LINEAGE, None)
    assert seen.selector_present is False and seen.selector_ref is None and seen.found == ()


def test_a_selector_dot_in_a_path_is_matched_exactly(rig) -> None:
    lineage = Lineage("r_x", NodePath(("a", "b.c")))
    assert reads.selector_name(lineage) == "trwr-r_x-a.b.c"
    assert reads.name_filter("trwr-r_x-a.b.c") == r"^/trwr-r_x-a\.b\.c$"  # `.` is a dot, not "any"
    rig.write(**{**rig.read(), "containers": [row("trwr-r_x-aXb.c")]})
    assert ContainerReads(rig.docker).observe(SPEC, lineage, "up").selector_present is False


def test_the_read_facet_has_no_effect_member() -> None:
    public = {n for n, m in inspect.getmembers(ContainerReads, inspect.isfunction) if n[0] != "_"}
    assert public == {"observe", "check", "endpoint"}
    assert core.read_protocol_violations(ports.ResourceReads) == []
    for name in public:
        assert "ticket" not in inspect.signature(getattr(ContainerReads, name)).parameters


@pytest.mark.proves("WR-ENV-2", "WR-ENV-2:route-refused-adapter", "B", "B", "LOGIC", "CI")
def test_local_target_endpoint_route_refused(rig) -> None:
    local = FoundRef("local_process", "found-123-456", datetime.now(UTC))
    reader = ContainerReads(rig.docker)
    for vantage in (Vantage.HOST, Vantage.CONTAINER):
        refused = reader.endpoint(local, vantage)
        assert isinstance(refused, ports.RouteRefused)
        assert refused.code == reads.ROUTE_UNSUPPORTED and refused.human_action
    assert rig.calls() == []  # never a best-effort address: docker was not even asked


def test_endpoint_host_is_the_published_port_and_container_the_name(rig) -> None:
    rig.write(
        **{**rig.read(), "containers": [row(SELECTOR, ports=[{"private": 8080, "host": 32901}])]}
    )
    ref = SelectorRef(LINEAGE, "up", SELECTOR, datetime.now(UTC))
    reader = ContainerReads(rig.docker)
    assert reader.endpoint(ref, Vantage.HOST) == ports.Endpoint("tcp", "127.0.0.1", 32901)
    assert reader.endpoint(ref, Vantage.CONTAINER) == ports.Endpoint("tcp", SELECTOR, 8080)
    assert set(rig.verbs()) == {"port"}


def test_endpoint_of_a_stopped_or_absent_target_is_refused_not_guessed(rig) -> None:
    rig.write(**{**rig.read(), "containers": [row(SELECTOR, state="exited")]})
    reader = ContainerReads(rig.docker)
    ref = SelectorRef(LINEAGE, "up", SELECTOR, datetime.now(UTC))
    refused = reader.endpoint(ref, Vantage.HOST)
    assert isinstance(refused, ports.RouteRefused) and refused.code == reads.ROUTE_UNSUPPORTED
    rig.write(**{**rig.read(), "reachable": False})
    down = reader.endpoint(ref, Vantage.HOST)
    assert isinstance(down, ports.RouteRefused) and down.code == engine.DOCKER_ENGINE_UNREACHABLE


def test_check_running_and_a_declared_exec_check(rig) -> None:
    rig.write(**{**rig.read(), "containers": [row(SELECTOR, exec_exit=1), row("other")]})
    checks = {"ready": ExecCheck(("pg_isready", "-h", "127.0.0.1"), {"PGPASSWORD": "fixture"})}
    reader = ContainerReads(rig.docker, checks)
    ref = SelectorRef(LINEAGE, "up", SELECTOR, datetime.now(UTC))
    assert reader.check("running", ref).satisfied is True
    not_ready = reader.check("ready", ref)
    assert not_ready.satisfied is False and not_ready.code is None
    rig.write(**{**rig.read(), "containers": [row(SELECTOR, exec_exit=0)]})
    assert reader.check("ready", ref).satisfied is True
    exec_calls = [c["args"] for c in rig.calls() if c["args"][0] == "exec"]
    assert exec_calls[-1] == [
        "exec",
        "-e",
        "PGPASSWORD=fixture",
        SELECTOR,
        "pg_isready",
        "-h",
        "127.0.0.1",
    ]
    assert reader.check("unknown-check", ref).satisfied is False
    assert set(rig.verbs()) <= READ_VERBS


def test_check_reaches_only_the_named_container(rig) -> None:
    rig.write(**{**rig.read(), "containers": [row(SELECTOR), row(SELECTOR + "2", state="exited")]})
    reader = ContainerReads(rig.docker)
    assert reader.check(
        "running", SelectorRef(LINEAGE, "up", SELECTOR, datetime.now(UTC))
    ).satisfied
    other = CreatedHandle(LINEAGE, "up", SELECTOR + "2", ports.InRunGroup())
    assert reader.check("running", other).satisfied is False


def test_a_missing_cli_and_an_unreachable_engine_could_not_observe(rig, tmp_path) -> None:
    ref = SelectorRef(LINEAGE, "up", SELECTOR, datetime.now(UTC))
    gone = engine.DockerCli(str(tmp_path / "no-such-docker"), None, rig.docker.execution)
    for docker, code in ((gone, engine.DOCKER_CLI_MISSING), (rig.docker, None)):
        if code is None:
            rig.write(**{**rig.read(), "reachable": False})
            code = engine.DOCKER_ENGINE_UNREACHABLE
        reader = ContainerReads(docker)
        seen = reader.observe(SPEC, LINEAGE, "up")
        assert seen.code == code and seen.selector_present is False and seen.selector_ref is None
        assert seen.found == ()
        checked = reader.check("running", ref)
        assert checked.satisfied is False and checked.code == code
        refused = reader.endpoint(ref, Vantage.HOST)
        assert isinstance(refused, ports.RouteRefused) and refused.code == code


def test_every_docker_invocation_goes_through_the_injected_port(rig) -> None:
    seen: list[tuple[str, ...]] = []

    class Recording:
        def run(self, command, ticket, cancel, until):  # type: ignore[no-untyped-def]
            seen.append(tuple(command.argv))
            assert command.environment == {} and command.argv[0] == rig.executable
            return rig.docker.execution.run(command, ticket, cancel, until)

    docker = engine.DockerCli(rig.executable, "unix:///e.sock", Recording())
    rig.write(
        **{**rig.read(), "containers": [row(SELECTOR, ports=[{"private": 80, "host": 8080}])]}
    )
    reader = ContainerReads(docker)
    reader.observe(SPEC, LINEAGE, "up")
    ref = SelectorRef(LINEAGE, "up", SELECTOR, datetime.now(UTC))
    reader.check("running", ref)
    reader.endpoint(ref, Vantage.HOST)
    assert seen and all(argv[1:3] == ("--host", "unix:///e.sock") for argv in seen)
    assert {argv[3] for argv in seen} <= READ_VERBS


def test_a_cancelled_read_is_could_not_observe_with_the_ports_code(rig) -> None:
    class Cancelled:
        requested = True

        def cause(self) -> Any:
            from trestle.workflow.values import StopCause

            return StopCause.CANCEL

        def wait(self, timeout: Any) -> bool:
            return True

    docker = engine.DockerCli(rig.executable, None, rig.docker.execution, Cancelled())
    rig.write(**{**rig.read(), "containers": [row(SELECTOR)]})
    seen = ContainerReads(docker).observe(SPEC, LINEAGE, "up")
    assert seen.code == "execution.cancelled" and seen.selector_present is False
