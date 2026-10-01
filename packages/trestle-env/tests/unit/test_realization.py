"""Realization spaces and root-entry levels are declared data, never selection code (L.NW-3.2)."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
from trestle.workflow.declarations import RealizationKind, Vantage

from trestle_env import realization as realization_module
from trestle_env.catalog import Catalog, load_reference
from trestle_env.realization import (
    ROOT_ENTRY_LEVELS,
    Realization,
    RootEntryLevel,
    RootEntryReading,
    realization_space,
    root_entry_levels,
)


def catalog() -> Catalog:
    return Catalog.from_data(
        {
            "schema": 1,
            "env_key": "env",
            "services": [{"id": "db", "selector": "db"}, {"id": "api", "selector": "api"}],
            "projects": [
                {"id": "py", "pin": {"python": "3.12"}, "tasks": [{"id": "serve", "argv": ["a"]}]}
            ],
            "overrides": [{"id": "api-local", "service": "api", "project": "py", "task": "serve"}],
        }
    )


@pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:reference-realization-space-declared", "B", "B", "LOGIC", "CI"
)
def test_space_is_data_only() -> None:
    space = realization_space(catalog(), "api")
    assert [r.kind for r in space] == [
        RealizationKind.DOCKER_SERVICE,
        RealizationKind.AGENT_LAUNCHED_PROJECT,
    ]  # declared preference order: the Docker service, then its local override
    docker, local = space
    assert docker.reachable_from == {Vantage.HOST, Vantage.CONTAINER}
    assert local.reachable_from == {
        Vantage.HOST
    }  # a container consumer cannot reach a host process
    assert docker.readiness == local.readiness == "api"  # one readiness contract for the space
    assert (local.override, local.project, local.task) == ("api-local", "py", "serve")
    assert realization_space(catalog(), "db")[0].override is None
    assert len(realization_space(catalog(), "db")) == 1
    assert realization_space(catalog(), "no-such") == ()
    for member in space:  # frozen data: no callable field, nothing to mutate
        assert dataclasses.is_dataclass(member) and member.__dataclass_params__.frozen
        assert not any(callable(getattr(member, f.name)) for f in dataclasses.fields(member))
    with pytest.raises(dataclasses.FrozenInstanceError):
        docker.kind = RealizationKind.PROVISIONED  # type: ignore[misc]
    assert realization_space(catalog(), "api") == space  # deterministic


def test_every_reference_service_has_a_docker_realization() -> None:
    reference = load_reference()
    for service in reference.services:
        first = realization_space(reference, str(service.id))[0]
        assert first.kind is RealizationKind.DOCKER_SERVICE and first.human_action is None


def test_the_module_holds_no_selection_code() -> None:
    tree = ast.parse(Path(realization_module.__file__).read_text(encoding="utf-8"))
    functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert functions == {
        "realization_space",
        "choice_for",
        "root_entry_levels",
    }  # data only: no chooser
    assert Realization.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_root_entry_levels_are_declared_under_both_readings() -> None:
    assert all(isinstance(level, RootEntryLevel) for level in ROOT_ENTRY_LEVELS)
    both = root_entry_levels()
    assert set(both) == {"env_and_test", "system_test"}  # the fixtures eligible under both
    for reading in RootEntryReading:
        assert set(root_entry_levels(reading)) == set(both)
        assert "run_selector" not in root_entry_levels(reading)
    assert len(RootEntryReading) == 2
