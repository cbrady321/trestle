"""L.NW-2.4: the Container Control and Compose families' conformance suites, run against the
stdlib fakes (`FakeContainerEngine`, `FakeComposeResolver`). One suite file per family
(`tests/conformance/*_cases.py`), registered through `register_family` and run by `run_family`,
UNMODIFIED, against every implementation (WR-PROOF-4, SA-14). Planted defects prove the suite is
not vacuous. The real adapters register their own bindings in L.NW-2.5 - L.NW-2.7.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from conformance import compose_cases, container_cases
from tests.proof import tolerances
from tests.proof.host.docker_gate import fake_docker, inventory
from tests.proof.suites.ports import core
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind

from trestle_packs.container import ContainerDefinition, bind
from trestle_packs.fakes.compose import FakeComposeResolver
from trestle_packs.fakes.container import FakeContainerEngine
from trestle_packs.process.command import CommandPort

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPEC = ports.ResourceSpec("suite-db", RealizationKind.DOCKER_SERVICE, "suite-entry", None)


# ------------------------------------------------------------------------------- container


def _down(**knobs: Any) -> core.Implementation:
    engine = FakeContainerEngine(**knobs)
    return core.Implementation(
        engine,
        core.Reach(engine_inventory=engine.inventory),
        name="fake-container-down",
        extras={"run_argv": engine.run_argv},
    )


def fake_container(
    engine_class: type[FakeContainerEngine] = FakeContainerEngine,
) -> Callable[[], core.Implementation]:
    def build() -> core.Implementation:
        engine = engine_class()
        return core.Implementation(
            engine,
            core.Reach(engine_inventory=engine.inventory),
            name="fake",
            extras={
                "spec": SPEC,
                "lifetimes": ("run", "durable"),
                "executable": engine.executable,
                "endpoint": engine.docker_endpoint,
                "plant_found": engine.plant_found,
                "seed_volume": engine.seed_volume,
                "run_argv": engine.run_argv,
                "unreachable": lambda: _down(reachable=False),
                "missing_cli": lambda: _down(cli_present=False),
            },
            close=engine.close,
        )

    return build


# ------------------------------------------------------------------------------- real binding
#
# The real adapter, bound to a docker CLI, an endpoint and a pinned image. Two bindings run the ONE
# suite file: `real-shim` (CI: the absolute-path `fake_docker.py` shim, no engine) and `real` (the
# host-docker gate, `docker_host`: the operator's docker and the MC-B-10 alpine image the gate
# exports as `TRESTLE_IMAGE_ALPINE`, `TRESTLE_DOCKER_ENDPOINT`). What the family needs beyond the
# port (planting a found instance, seeding a volume, running a descriptor's argv, the engine
# inventory) is done here with plain docker calls, never with the adapter under test.

FIXTURE_LABEL = "trestle.proof.fixture=container-suite"
KEEP_RUNNING = ("sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done")
SELECTOR_ROOT = "^/trwr-" + container_cases.ROOT_RUN + "-"


class RealEngine:
    """One docker CLI bound to one endpoint and image: the suite's infrastructure calls."""

    def __init__(self, cli: str, endpoint: str | None, image: str) -> None:
        self.cli, self.endpoint, self.image = cli, endpoint, image
        self.volumes: list[str] = []

    def docker(self, *args: str) -> tuple[int, str]:
        host = ["--host", self.endpoint] if self.endpoint else []
        return self.run_argv([self.cli, *host, *args])

    def run_argv(self, argv: Sequence[str]) -> tuple[int, str]:
        try:
            done = subprocess.run(  # noqa: S603 - the suite's own docker, an absolute path
                list(argv),
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=inventory.docker_env(),
                timeout=tolerances.JOIN_WAIT_S,
                check=False,
            )
        except FileNotFoundError:
            return 127, ""
        return done.returncode, done.stdout

    def plant_found(self, system: str, running: bool = True) -> str:
        verb = "run" if running else "create"
        code, out = self.docker(
            verb, *(["-d"] if running else []), "--pull", "never", "--name", system,
            "--label", FIXTURE_LABEL, self.image, *KEEP_RUNNING,
        )  # fmt: skip
        assert code == 0, out
        return system

    def seed_volume(self, name: str) -> None:
        code, out = self.docker("volume", "create", "--label", FIXTURE_LABEL, name)
        assert code == 0, out
        self.volumes.append(name)

    def cleanup(self) -> None:
        """Remove what one case left: this root's selectors, the planted found instance, the seeded
        volumes (fixture-labelled; CSC-10). Never an unattributable object."""
        for name_filter in (SELECTOR_ROOT, "^/suite-db$"):
            _, ids = self.docker("ps", "-aq", "--no-trunc", "--filter", f"name={name_filter}")
            for cid in ids.split():
                self.docker("rm", "-f", cid)
        for volume in self.volumes:
            self.docker("volume", "rm", volume)
        self.volumes.clear()


def _real_definitions(image: str) -> dict[str, ContainerDefinition]:
    return {
        "suite-entry": ContainerDefinition(
            image, command=KEEP_RUNNING, data_paths=("/data",), ports=(8080,)
        )
    }


def real_container(
    cli: str,
    endpoint: str | None,
    image: str,
    inventory_of: Callable[[], dict[str, frozenset[str]]],
    down_of: Callable[[], tuple[str, str | None]],
) -> Callable[[], core.Implementation]:
    """A factory of `real` implementations. `down_of` names the CLI and endpoint of an engine that
    does not answer (the same descriptor argv is run against it)."""

    def build() -> core.Implementation:
        engine = RealEngine(cli, endpoint, image)
        port = bind(cli, endpoint, CommandPort(), definitions=_real_definitions(image)).containers

        def unreachable() -> core.Implementation:
            down_cli, down_endpoint = down_of()
            dead = RealEngine(down_cli, down_endpoint, image)
            broken = bind(down_cli, down_endpoint, CommandPort()).containers

            def rebind(argv: Sequence[str]) -> tuple[int, str]:
                moved = [down_cli, *argv[1:]]
                if "--host" in moved and down_endpoint is not None:
                    moved[moved.index("--host") + 1] = down_endpoint
                return dead.run_argv(moved)

            return core.Implementation(broken, name="real-unreachable", extras={"run_argv": rebind})

        def missing_cli() -> core.Implementation:
            absent = str(Path(cli).parent / "no-such-docker")
            broken = bind(absent, endpoint, CommandPort()).containers
            return core.Implementation(broken, name="real-missing-cli")

        return core.Implementation(
            port,
            core.Reach(engine_inventory=inventory_of),
            name="real",
            extras={
                "spec": SPEC,
                "lifetimes": ("run", "durable"),
                "executable": cli,
                "endpoint": endpoint,
                "plant_found": engine.plant_found,
                "seed_volume": engine.seed_volume,
                "run_argv": engine.run_argv,
                "unreachable": unreachable,
                "missing_cli": missing_cli,
            },
            close=engine.cleanup,
        )

    return build


def shim_inventory(state: Path) -> Callable[[], dict[str, frozenset[str]]]:
    def read() -> dict[str, frozenset[str]]:
        data = fake_docker.read_state(state)
        return {
            "containers": frozenset(n for c in data["containers"] for n in c["names"]),
            "images": frozenset(f"{i['repository']}:{i['tag']}" for i in data["images"]),
            "volumes": frozenset(v["name"] for v in data["volumes"]),
            "networks": frozenset(n["name"] for n in data["networks"]),
        }

    return read


def docker_inventory(cli: str, endpoint: str | None) -> Callable[[], dict[str, frozenset[str]]]:
    def read() -> dict[str, frozenset[str]]:
        snap = inventory.snapshot(cli, endpoint)
        assert snap["engine"]["reachable"], snap["engine"]
        return {
            kind: frozenset(
                name
                for obj in snap[kind]
                for name in (
                    inventory.object_names(obj) if kind != "images" else [inventory._key(kind, obj)]
                )
            )
            for kind in inventory.KINDS
        }

    return read


def real_shim_container(base: Path) -> Callable[[], core.Implementation]:
    """The real adapter over `CommandPort` and the shim: one fresh engine state per case."""
    counter = iter(range(10_000))

    def build() -> core.Implementation:
        directory = base / f"engine-{next(counter)}"
        directory.mkdir()
        state = directory / "state.json"
        fake_docker.write_state(
            state,
            reachable=True,
            server_version="29.8.0",
            containers=[],
            images=[{"id": "sha256:aa", "repository": "alpine", "tag": "3.20", "repo_digests": []}],
            volumes=[],
            networks=[],
        )
        cli = fake_docker.install_shim(directory, state)
        dead_dir = directory / "down"
        dead_dir.mkdir()
        dead_state = dead_dir / "state.json"
        fake_docker.write_state(dead_state, reachable=False)
        dead_cli = fake_docker.install_shim(dead_dir, dead_state)
        endpoint = "unix:///fake/desktop-linux.sock"
        return real_container(
            cli, endpoint, "alpine:3.20", shim_inventory(state), lambda: (dead_cli, endpoint)
        )()

    return build


def real_docker_container(base: Path) -> Callable[[], core.Implementation]:
    """The real adapter over the operator's docker at the gate's endpoint (`docker_host`)."""
    cli = shutil.which("docker")  # the test names the operator's path; the adapter never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    endpoint = os.environ.get("TRESTLE_DOCKER_ENDPOINT")
    image = os.environ[
        "TRESTLE_IMAGE_ALPINE"
    ]  # `<repo>@sha256:<hex>`, exported by `docker_gate run`
    absent = f"unix://{base}/absent.sock"
    return real_container(
        cli, endpoint, image, docker_inventory(cli, endpoint), lambda: (cli, absent)
    )


CONTAINER_BINDINGS = [
    pytest.param(
        "fake",
        id="fake",
        marks=[
            pytest.mark.proves(
                "WR-PROOF-4", "WR-PROOF-4:b-docker-fake-passes-suite", "B", "B", "LOGIC", "CI"
            ),
            pytest.mark.proves(
                "WR-VERIFY-8", "WR-VERIFY-8:b-docker-fake-read-facets", "B", "B", "LOGIC", "CI"
            ),
            # L.NW-2.5: the fake twin of the real read cases
            pytest.mark.stub_proven(
                "WR-OWN-7:b-identity-not-port-occupancy-adapter@host@stub-twin"
            ),
            pytest.mark.stub_proven("WR-ENV-2:route-refused-adapter@host@stub-twin"),
            # L.NW-2.6: the fake twin of the real effect cases
            pytest.mark.stub_proven("WR-OWN-4:b-port-stop-never-removes-volume@host@stub-twin"),
            pytest.mark.stub_proven("WR-PROOF-4:b-descriptor-before-effect@host@stub-twin"),
        ],
    ),
    pytest.param("real-shim", id="real-shim"),
    pytest.param(
        "real",
        id="real",
        marks=[
            pytest.mark.docker_host,
            pytest.mark.proves(
                "WR-OWN-7",
                "WR-OWN-7:b-identity-not-port-occupancy-adapter@host",
                "B",
                "B",
                "DOCKER",
                "HOST",
            ),
            pytest.mark.proves(
                "WR-ENV-2", "WR-ENV-2:route-refused-adapter@host", "B", "B", "DOCKER", "HOST"
            ),
            pytest.mark.proves(
                "WR-OWN-4",
                "WR-OWN-4:b-port-stop-never-removes-volume@host",
                "B",
                "B",
                "DOCKER",
                "HOST",
            ),
            pytest.mark.proves(
                "WR-PROOF-4",
                "WR-PROOF-4:b-descriptor-before-effect@host",
                "B",
                "B",
                "DOCKER",
                "HOST",
            ),
        ],
    ),
]


def container_factory(binding: str, base: Path) -> Callable[[], core.Implementation]:
    factories: dict[str, Callable[[Path], Callable[[], core.Implementation]]] = {
        "fake": lambda _: fake_container(),
        "real-shim": real_shim_container,
        "real": real_docker_container,
    }
    return factories[binding](base)


@pytest.mark.parametrize("binding", CONTAINER_BINDINGS)
def test_container_port_suite(binding: str, tmp_path: Path) -> None:
    run = core.run_family(container_cases.FAMILY, container_factory(binding, tmp_path))
    assert run.cases_run == tuple(c.name for c in container_cases.CASES)
    assert run.suite_sha256 == core.sha256_of(Path(container_cases.__file__))


# planted defects: each fake has one, and the suite (unmodified) names the case that catches it


class _RemoveNone(FakeContainerEngine):
    def release_descriptor(self, call: Any) -> Any:
        wire = super().release_descriptor(call)
        return {**wire, "remove_argv": None} if wire.get("form") == "argv" else wire


class _VolumeFlag(FakeContainerEngine):
    def release_descriptor(self, call: Any) -> Any:
        wire = super().release_descriptor(call)
        if wire.get("form") == "argv":
            *head, name = wire["remove_argv"]
            return {**wire, "remove_argv": [*head, "-v", name]}
        return wire


class _StopDeletesVolumes(FakeContainerEngine):
    def stop(self, target: Any, ticket: Any) -> Any:
        self._volumes.clear()
        return super().stop(target, ticket)


class _CheckStartsAContainer(FakeContainerEngine):
    def check(self, check: str, target: Any) -> Any:
        self.plant_found("side-effect")  # a read that changes the engine's inventory
        return super().check(check, target)


class _UnreachableReadsAbsent(FakeContainerEngine):
    def _down(self) -> str | None:
        return None  # the engine is down but the read says "absent", not "could not observe"


class _CreateNotIdempotent(FakeContainerEngine):
    def create(self, spec: Any, ticket: Any) -> Any:
        confirmation = super().create(spec, ticket)
        if ticket.attempt > 1:
            self.plant_found(f"{confirmation.identity}-second")
        return confirmation


class _FoundIsPresent(FakeContainerEngine):
    def observe(self, spec: Any, lineage: Any, effect: str | None) -> Any:
        seen = super().observe(spec, lineage, effect)
        if seen.found and not seen.selector_present:
            return type(seen)(True, seen.selector_ref, True, True, (), (), None)  # occupancy
        return seen


class _RunArgvExitsZeroWhenDown(FakeContainerEngine):
    def run_argv(self, argv: Any) -> tuple[int, str]:
        code, out = super().run_argv(argv)
        return (0, "") if not self.reachable and self.cli_present else (code, out)


@pytest.mark.parametrize(
    ("defect", "caught_by"),
    [
        (_RemoveNone, "descriptor_forms_by_lifetime"),
        (_VolumeFlag, "argv_release_is_the_pinned_shape"),
        (_StopDeletesVolumes, "stop_removes_the_container_and_no_volume"),
        (_CheckStartsAContainer, "check_and_endpoint_reach_only_the_named_instance"),
        (_CreateNotIdempotent, "create_idempotent_per_selector"),
        (_FoundIsPresent, "found_instance_only_in_found"),
    ],
)
def test_container_suite_catches_planted_defects(
    defect: type[FakeContainerEngine], caught_by: str
) -> None:
    with pytest.raises((core.SuiteFailure, core.ReadMutation)) as caught:
        core.run_family(container_cases.FAMILY, fake_container(defect))
    assert caught_by in str(caught.value)


def test_a_down_engine_read_as_absent_is_caught() -> None:
    # `unreachable()` builds the base class; plant the defect in the factory's down engines too
    def factory() -> core.Implementation:
        built = fake_container(_UnreachableReadsAbsent)()
        return core.Implementation(
            built.impl,
            built.reach,
            built.name,
            {**built.extras, "unreachable": lambda: _down_of(_UnreachableReadsAbsent)},
            built.close,
        )

    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(container_cases.FAMILY, factory)
    assert "reads_against_an_unreachable_engine_could_not_observe" in str(caught.value)


def _down_of(engine_class: type[FakeContainerEngine]) -> core.Implementation:
    engine = engine_class(reachable=False)
    return core.Implementation(
        engine, core.Reach(engine_inventory=engine.inventory), extras={"run_argv": engine.run_argv}
    )


def test_an_unreachable_engine_read_as_absent_by_the_observe_argv_is_caught() -> None:
    def factory() -> core.Implementation:
        built = fake_container()()
        return core.Implementation(
            built.impl,
            built.reach,
            built.name,
            {
                **built.extras,
                "unreachable": lambda: core.Implementation(
                    _RunArgvExitsZeroWhenDown(reachable=False),
                    extras={"run_argv": _RunArgvExitsZeroWhenDown(reachable=False).run_argv},
                ),
            },
            built.close,
        )

    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(container_cases.FAMILY, factory)
    assert "observe_argv_reads_absent_only_for_a_missing_selector" in str(caught.value)


def test_the_planted_run_argv_defect_is_inert_on_a_live_engine() -> None:
    live = _RunArgvExitsZeroWhenDown()
    assert live.run_argv([live.executable, "ps", "-aq"]) == (0, "")


# ------------------------------------------------------------------------------- compose


def fake_compose(base: Path) -> core.Implementation:
    shutil.copy(FIXTURES / "compose-chain.yaml", base / "chain.json")
    shutil.copy(FIXTURES / "compose-changed.yaml", base / "changed.json")
    (base / "invalid.json").write_text("{not json", encoding="utf-8")
    big = {
        "services": {
            f"s{i}": {"depends_on": [f"s{i + 1}"] if i < 1100 else []} for i in range(1101)
        }
    }
    (base / "oversized.json").write_text(json.dumps(big), encoding="utf-8")
    projects = {
        name: base / f"{name}.json" for name in ("chain", "changed", "invalid", "oversized")
    }
    resolver = FakeComposeResolver(projects)
    return core.Implementation(
        resolver,
        core.Reach(envelope=base),
        name="fake",
        extras={
            "project": "chain",
            "changed_project": "changed",
            "invalid_project": "invalid",
            "oversized_project": "oversized",
        },
    )


COMPOSE_BINDINGS = [
    pytest.param("fake", id="fake"),
]


@pytest.mark.parametrize("binding", COMPOSE_BINDINGS)
def test_compose_resolver_suite(binding: str, tmp_path: Path) -> None:
    run = core.run_family(compose_cases.FAMILY, lambda: fake_compose(_fresh(tmp_path)))
    assert run.cases_run == tuple(c.name for c in compose_cases.CASES)


def _fresh(base: Path) -> Path:
    n = len(list(base.glob("run-*")))
    directory = base / f"run-{n}"
    directory.mkdir()
    return directory


class _WritesWhileReading(FakeComposeResolver):
    def closure(self, project: str, selected: frozenset[str]) -> Any:
        path = self.projects.get("chain")
        if path is not None:
            (path.parent / "scratch.txt").write_text("a read that leaves a file behind")
        return super().closure(project, selected)


def test_compose_suite_catches_a_read_that_writes(tmp_path: Path) -> None:
    def factory() -> core.Implementation:
        built = fake_compose(_fresh(tmp_path))
        return core.Implementation(
            _WritesWhileReading(built.impl.projects), built.reach, built.name, built.extras
        )

    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(compose_cases.FAMILY, factory)
    assert "filesystem change" in str(caught.value)


class _DropsEdges(FakeComposeResolver):
    def closure(self, project: str, selected: frozenset[str]) -> Any:
        result = super().closure(project, selected)
        return replace_edges(result)


def replace_edges(result: Any) -> Any:
    if hasattr(result, "edges"):
        return type(result)(result.services, frozenset(), result.definition_fingerprint)
    return result


def test_compose_suite_catches_a_closure_without_its_edges(tmp_path: Path) -> None:
    def factory() -> core.Implementation:
        built = fake_compose(_fresh(tmp_path))
        return core.Implementation(
            _DropsEdges(built.impl.projects), built.reach, built.name, built.extras
        )

    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(compose_cases.FAMILY, factory)
    assert "closure_of_a_chain_is_its_transitive_dependencies" in str(caught.value)
