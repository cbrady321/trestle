"""Each stage's failure names its stage and its service (L.RB-2.2; WR-ENV-9)."""

from __future__ import annotations

import pytest
from trestle.common.plan import compiler, vocabulary
from trestle.workflow import ports
from trestle.workflow.extract import extract_declared_tree
from trestle.workflow.ports import Closure

from trestle_env import schema, stages, tree
from trestle_env.plugins._bind import ClosureRefusedError, derive_closure
from trestle_env.stages import Stage

CODE = vocabulary.POSTCONDITION_TIMEOUT


def catalog_failure() -> stages.StageFailure:
    request = {schema.ENV_ARG: "e", schema.SERVICES_ARG: ["mongo"]}
    refused = compiler.compile(extract_declared_tree(tree.ENTRY), request)
    assert isinstance(refused, compiler.Refusal)
    assert refused.valid_listed_at == f"identifier_sets.{tree.SERVICES_SET}"
    return stages.catalog_failure(refused.code, refused.identifier)


def closure_failure() -> stages.StageFailure:
    class Refuses:
        def closure(self, project: str, selected: frozenset[str]) -> Closure:
            return Closure(frozenset({"postgres", "mystery"}), frozenset(), "fp")

    with pytest.raises(ClosureRefusedError) as raised:
        derive_closure({ports.ComposeResolver: Refuses()}, ["postgres"])
    return raised.value.failure


CASES = {
    "catalog": (catalog_failure, Stage.CATALOG, "mongo"),
    "closure": (closure_failure, Stage.CLOSURE, "mystery"),
    "readiness": (
        lambda: stages.failure_at(tree.POSTGRES_SERVICE, CODE),
        Stage.READINESS,
        "postgres",
    ),
    "provisioning": (
        lambda: stages.failure_at("provision.postgres", CODE),
        Stage.PROVISIONING,
        "postgres",
    ),
    "test": (lambda: stages.failure_at("test.system", CODE), Stage.TEST, "system"),
}


@pytest.mark.proves("WR-ENV-9", "WR-ENV-9:stage-k-named", "B", "B", "LOGIC", "CI")
@pytest.mark.parametrize("stage", list(CASES))
def test_each_stage_failure_named(stage: str) -> None:
    build, expected, service = CASES[stage]
    failure = build()
    assert failure is not None
    assert failure.stage is expected and failure.service == service
    text = failure.text()
    assert expected.value in text and service in text  # both are named, in one bounded line
    assert len(text) <= 200


def test_every_declared_node_names_its_stage_and_a_catalog_service() -> None:
    services = tree.identifier_sets(tree.CATALOG)[tree.SERVICES_SET]
    for service in (tree.HTTP_SUPPORT_SERVICE, tree.POSTGRES_SERVICE):
        failure = stages.failure_at(service, CODE)
        assert failure is not None and failure.stage is Stage.READINESS
        assert failure.service in services  # the service the failure names is a catalog identifier


def test_a_path_that_is_no_stage_node_names_no_stage() -> None:
    for path in ("", "reference_env", "other.postgres", "backend.", ".postgres"):
        assert stages.failure_at(path, CODE) is None, path
    # a nested node is named by its last segment
    assert stages.failure_at(f"group/{tree.POSTGRES_SERVICE}", CODE) == stages.StageFailure(
        Stage.READINESS, "postgres", CODE
    )
