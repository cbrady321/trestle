"""L.NW-2.4: the Container Control and Compose families' conformance suites, run against the
stdlib fakes (`FakeContainerEngine`, `FakeComposeResolver`). One suite file per family
(`tests/conformance/*_cases.py`), registered through `register_family` and run by `run_family`,
UNMODIFIED, against every implementation (WR-PROOF-4, SA-14). Planted defects prove the suite is
not vacuous. The real adapters register their own bindings in L.NW-2.5 - L.NW-2.7.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conformance import compose_cases, container_cases
from tests.proof.suites.ports import core
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind

from trestle_packs.fakes.compose import FakeComposeResolver
from trestle_packs.fakes.container import FakeContainerEngine

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
        ],
    ),
]
CONTAINER_FACTORIES: dict[str, Callable[[], core.Implementation]] = {"fake": fake_container()}


@pytest.mark.parametrize("binding", CONTAINER_BINDINGS)
def test_container_port_suite(binding: str) -> None:
    run = core.run_family(container_cases.FAMILY, CONTAINER_FACTORIES[binding])
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
