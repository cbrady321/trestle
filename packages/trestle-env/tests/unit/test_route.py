"""The composition root routes the shared port protocols by realization (L.RB-6.2; B3-C21)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from trestle.workflow.declarations import RealizationKind
from trestle.workflow.ports import ResourceSpec
from trestle.workflow.values import FoundRef

from trestle_env import tree
from trestle_env.catalog import REFERENCE_PATH, Catalog
from trestle_env.plugins._route import RECORD_KIND, RealizationRouter


class Which:
    def __init__(self, name: str) -> None:
        self.name = name

    def observe(self, spec: Any, lineage: Any, effect: Any) -> str:
        return self.name

    def check(self, check: Any, target: Any) -> str:
        return self.name

    def endpoint(self, target: Any, vantage: Any) -> str:
        return self.name

    def launch_policy(self, spec: Any) -> str:
        return self.name

    def release_descriptor(self, call: Any) -> str:
        return self.name

    def create(self, spec: Any, ticket: Any) -> str:
        return self.name


def spec(kind: RealizationKind) -> ResourceSpec:
    return ResourceSpec("postgres", kind, "entry", None)


def test_a_call_is_routed_by_its_spec_or_its_target() -> None:
    router = RealizationRouter(Which("containers"), Which("provisioning"))
    docker, record = spec(RealizationKind.DOCKER_SERVICE), spec(RealizationKind.PROVISIONED)
    assert router.observe(docker, None, "up") == "containers"
    assert router.observe(record, None, "submit") == "provisioning"
    assert (
        router.create(record, None) == "provisioning"
        and router.create(docker, None) == "containers"
    )
    assert router.launch_policy(record) == "provisioning"
    found = FoundRef(RECORD_KIND, "trwr-r-x", datetime.now(UTC))
    other = FoundRef("docker_container", "postgres", datetime.now(UTC))
    assert router.check("anything", found) == "provisioning"
    assert router.check("recorded", other) == "provisioning"  # the provisioning port's own check id
    assert router.check("postgres_ready", other) == "containers"
    assert (
        router.endpoint(found, None) == "provisioning"
        and router.endpoint(other, None) == "containers"
    )


def test_a_catalog_test_that_needs_provisioning_adds_the_node_before_the_test() -> None:
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    data["tests"] = [
        {"id": "system", "project": "system-tests", "task": "run", "provision": True},
        {"id": "demo-version", "project": "demo-py", "task": "version"},
    ]
    entry = tree.build_entry(Catalog.from_data(data))
    root = entry.units[tree.ROOT_UNIT]
    needs = {c.unit: c.needs for c in root.children}  # type: ignore[union-attr]
    assert needs[tree.PROVISION_UNIT] == (tree.POSTGRES_UNIT,)
    assert tree.PROVISION_UNIT in needs["test.system"]
    assert tree.PROVISION_UNIT not in needs["test.demo-version"]
    assert tree.PROVISION_UNIT in entry.units
    assert tree.PROVISION_UNIT not in tree.ENTRY.units  # the reference catalog provisions nothing


def test_the_provisioning_tree_fits_its_budgets() -> None:
    from trestle.common.plan import carving, compiler
    from trestle.workflow.extract import extract_declared_tree

    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    data["tests"] = [{"id": "system", "project": "system-tests", "task": "run", "provision": True}]
    entry = tree.build_entry(Catalog.from_data(data))
    plan = compiler.compile(extract_declared_tree(entry), {"env": "budget"})
    assert isinstance(plan, compiler.AdmittedPlan), plan
    slices = carving.carve(
        plan, float(tree.DEADLINE_S), 10.0, carving.release_slice_for(plan, 10.0)
    )
    assert not isinstance(slices, compiler.Refusal), slices  # backend -> provision -> test fits
    limits = carving.MarginLimits(grace=10.0, kill=5.0, sweep_parallelism=4)
    assert carving.margin_needed(plan, limits) <= 35.0  # the durable submit adds no release rank
