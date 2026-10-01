"""The reference tree at v0: declared units, admitted plan, authenticated readiness (L.RB-0.2)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from trestle.common.plan import carving, compiler
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.compiler import AdmittedPlan
from trestle.workflow.declarations import (
    AllDeclaration,
    LeafDeclaration,
    RealizationKind,
    WorkflowEntry,
)
from trestle.workflow.extract import extract_declared_tree
from trestle.workflow.ports import ResourceSpec

import trestle_env
from trestle_env import schema, tree

PROJECT = "refproj"
TREE_SOURCE = Path(tree.__file__)


def compiled(entry: WorkflowEntry, request: dict[str, Any]) -> AdmittedPlan:
    plan = compiler.compile(extract_declared_tree(entry), request)
    assert isinstance(plan, AdmittedPlan), plan
    return plan


def fresh_entry(name: str) -> WorkflowEntry:
    """`tree.ENTRY` of a fresh execution of tree.py: what a second publication imports."""
    spec = importlib.util.spec_from_file_location(name, TREE_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # a dataclass resolves its module through sys.modules
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[name]
    entry = module.ENTRY
    assert isinstance(entry, WorkflowEntry)
    return entry


def leaf() -> LeafDeclaration:
    unit = tree.ENTRY.units[tree.POSTGRES_UNIT]
    declared = unit.declare()  # type: ignore[attr-defined]
    assert isinstance(declared, LeafDeclaration)
    return declared


@pytest.mark.proves("WR-ENV-10", "WR-ENV-10:tree-declared-admitted", "B", "B", "LOGIC", "CI")
def test_declared_tree_admitted_one_root_one_child() -> None:
    # (the name is L.RB-0.2's: the tree began as one root over one child and has grown since;
    # what it pins is that the declared tree compiles to an admitted plan with the env key)
    plan = compiled(tree.ENTRY, {schema.ENV_ARG: PROJECT})
    assert [v.path for v in plan.vertices] == ["", tree.HTTP_SUPPORT_UNIT, tree.POSTGRES_UNIT]
    assert [v.unit for v in plan.vertices] == [
        tree.ROOT_UNIT,
        tree.HTTP_SUPPORT_UNIT,
        tree.POSTGRES_UNIT,
    ]
    assert plan.vertex("").children == (tree.HTTP_SUPPORT_UNIT, tree.POSTGRES_UNIT)
    assert plan.vertex(tree.POSTGRES_UNIT).compose == "leaf"
    # the environment key is the Compose project the request names (opaque bytes to the host)
    root = tree.ENTRY.units[tree.ROOT_UNIT]
    assert isinstance(root, AllDeclaration)
    assert root.env_key_field == schema.ENV_ARG
    assert plan.lease_set == (json.dumps(PROJECT),)  # the canonical JSON of the string
    # the plan is stable across two publications (a fresh import of the same source)
    again = compiled(fresh_entry("tree_v0_second_publication"), {schema.ENV_ARG: PROJECT})
    assert again.plan_digest == plan.plan_digest
    assert again.declaration_digest == plan.declaration_digest
    # the root declares its environment: a request without it never compiles
    refused = compiler.compile(extract_declared_tree(tree.ENTRY), {})
    assert isinstance(refused, compiler.Refusal)
    assert refused.code == vocab.LEASE_SET_UNDECIDABLE
    assert refused.identifier == schema.ENV_ARG


def test_the_declared_tree_fits_its_own_budgets() -> None:
    plan = compiled(tree.ENTRY, {schema.ENV_ARG: PROJECT})
    release = carving.release_slice_for(plan, 10.0)
    assert release > 0  # the create is run-lifetime: the root has a release walk
    slices = carving.carve(plan, float(tree.DEADLINE_S), 10.0, release)
    assert not isinstance(slices, compiler.Refusal), slices
    limits = carving.MarginLimits(grace=10.0, kill=5.0, sweep_parallelism=4)
    assert carving.margin_needed(plan, limits) <= 35.0  # the default finalization margin


def test_postgres_realization_is_one_docker_service() -> None:
    declared = leaf()
    assert declared.unit == tree.POSTGRES_UNIT
    assert declared.postcondition == tree.POSTGRES_READY
    unit = tree.ENTRY.units[tree.POSTGRES_UNIT]
    spec = unit._spec  # type: ignore[attr-defined]
    assert isinstance(spec, ResourceSpec)
    assert spec.realization is RealizationKind.DOCKER_SERVICE
    assert spec.logical_system == tree.POSTGRES_SERVICE == spec.entry
    assert spec.command is None


def test_postgres_readiness_is_authenticated_select() -> None:
    check = tree.READINESS[leaf().postcondition]
    assert check is tree.POSTGRES_READINESS
    text = " ".join(check.argv)
    # authenticated: `psql` (never `pg_isready`, which does not authenticate) asking for a query
    assert "psql" in text and "SELECT 1" in text
    assert "pg_isready" not in text
    assert f"-U {tree.POSTGRES_USER}" in text and f"-d {tree.POSTGRES_DATABASE}" in text
    # over TCP to the container's own non-loopback address, never a loopback name or a socket
    assert re.search(r'-h "\$\(hostname -i', text), text
    for loopback in ("127.0.0.1", "localhost", "::1", "/var/run", ".s.PGSQL"):
        assert loopback not in text, loopback
    # the password reaches psql through the exec environment and appears nowhere in the argv
    assert check.environment == {"PGPASSWORD": tree.POSTGRES_FIXTURE_PASSWORD}
    assert tree.POSTGRES_FIXTURE_PASSWORD not in text
    # the tree declares no other readiness path: no sleep, no port-open
    assert set(tree.READINESS) == {tree.POSTGRES_READY}


def test_unknown_service_is_refused_at_compile_naming_it_and_where_valid_ones_are_listed() -> None:
    request = {schema.ENV_ARG: PROJECT, schema.SERVICES_ARG: ["postgres", "mongo"]}
    refused = compiler.compile(extract_declared_tree(tree.ENTRY), request)
    assert isinstance(refused, compiler.Refusal)
    assert refused.code == vocab.UNKNOWN_IDENTIFIER
    assert refused.identifier == "mongo"
    assert refused.valid_listed_at == f"identifier_sets.{tree.SERVICES_SET}"
    known = {schema.ENV_ARG: PROJECT, schema.SERVICES_ARG: ["postgres"]}
    assert isinstance(compiler.compile(extract_declared_tree(tree.ENTRY), known), AdmittedPlan)


def test_the_identifier_sets_are_the_catalogs() -> None:
    sets = tree.ENTRY.units[tree.ROOT_UNIT].identifier_sets  # type: ignore[union-attr]
    assert sets == tree.identifier_sets(tree.CATALOG)
    assert sets[tree.SERVICES_SET] == {str(s.id) for s in tree.CATALOG.services}
    assert tree.POSTGRES_SERVICE in sets[tree.SERVICES_SET]


def test_the_tree_module_binds_no_adapter() -> None:
    source = TREE_SOURCE.read_text(encoding="utf-8")
    assert "trestle_packs" not in source  # bound by the composition root only
    assert trestle_env.__file__ is not None


def test_a_catalog_test_node_needs_every_readiness_node_and_backends_are_siblings() -> None:
    from twin.twin_engine import TEST_NODE, entry_with_test

    entry = entry_with_test()
    root = entry.units[tree.ROOT_UNIT]
    assert isinstance(root, AllDeclaration)
    needs = {c.unit: c.needs for c in root.children}
    assert needs[tree.HTTP_SUPPORT_UNIT] == () and needs[tree.POSTGRES_UNIT] == ()
    assert set(needs[TEST_NODE]) == {tree.HTTP_SUPPORT_UNIT, tree.POSTGRES_UNIT}
    plan = compiled(entry, {schema.ENV_ARG: PROJECT})
    assert set(plan.vertex(TEST_NODE).needs) == {tree.HTTP_SUPPORT_UNIT, tree.POSTGRES_UNIT}
    # the reference catalog lists no test: the default tree is the two backends
    assert [c.unit for c in tree.ENTRY.units[tree.ROOT_UNIT].children] == [  # type: ignore[union-attr]
        tree.HTTP_SUPPORT_UNIT,
        tree.POSTGRES_UNIT,
    ]
