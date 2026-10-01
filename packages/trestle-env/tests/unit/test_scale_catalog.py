"""A synthetic 500-service catalog with 100 selected (L.RB-1.3; WR-ENV-12, B2.1).

The catalog and its Compose definition come from the seeded generator
`fixtures/scale/gen_catalog.py`. The reference tree is re-declared over the generated catalog's
identifier sets (the root the plugin publishes, `tree.ENTRY`, with only `identifier_sets`
replaced), so the request binds 100 of 500 catalog identifiers exactly as a reference request
binds its own. Resolution is what admission and the plugin do before any effect: load the catalog,
derive the Compose closure of the selection (`FakeComposeResolver`, the fake binding), plan it
(`closure()`), and compile the tree against the request (MC-23).

* identity: two independent generations of the same seed give the same admitted plan digest,
  declaration digest, closure plan and definition fingerprint, whatever order the selection is
  given in;
* answer: the MCP host's answer to the 500/100 request stays within the run's summary budget
  (fake binding);
* doubling: the per-service cost of resolving the same request against 2N services is within
  MC-09's ratio of its cost against N (a throughput ratio, `tolerances.append_cost_ratio()`, as
  test_cs1_framing uses it): `(2N / cost(2N)) / (N / cost(N)) >= ratio`. Cost is the median of
  repeated measurements; the test holds no timing literal (SA-05).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
import statistics
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.proof import tolerances
from trestle.common.plan import bounds, compiler
from trestle.common.plan.compiler import AdmittedPlan
from trestle.workflow.declarations import AllDeclaration, WorkflowEntry
from trestle.workflow.extract import extract_declared_tree
from trestle_packs.fakes.compose import FakeComposeResolver

from trestle_env import schema, tree
from trestle_env.catalog import Catalog
from trestle_env.closure import ClosurePlan, closure

SCALE = Path(__file__).resolve().parents[1] / "fixtures" / "scale"
GEN = SCALE / "gen_catalog.py"
PLUGIN = SCALE / "scale_env.py"  # the reference plugin over the generated catalog
N = 500
SELECTED = 100
SEED = 20260930
ENV = "scale-env"
REPEATS = 15


def gen_catalog() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scale_gen_catalog", GEN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass resolves its module through sys.modules
    spec.loader.exec_module(module)
    return module


def entry_over(catalog: Catalog) -> WorkflowEntry:
    """`tree.ENTRY` with the root's identifier sets drawn from `catalog`."""
    root = tree.ENTRY.units[tree.ROOT_UNIT]
    assert isinstance(root, AllDeclaration)
    units = dict(tree.ENTRY.units)
    units[tree.ROOT_UNIT] = dataclasses.replace(root, identifier_sets=tree.identifier_sets(catalog))
    return dataclasses.replace(tree.ENTRY, units=units)


def resolve(synthetic: Any, selection: list[str], directory: Path) -> tuple[AdmittedPlan, Any]:
    """Load, close, plan and compile: everything resolution does before an effect."""
    catalog = Catalog.loads(synthetic.catalog_text())
    resolver = FakeComposeResolver({"scale": synthetic.write_compose(directory)})
    derived = resolver.closure("scale", frozenset(selection))
    planned = closure(catalog, derived, selection)
    assert isinstance(planned, ClosurePlan), planned
    request = {schema.ENV_ARG: ENV, schema.SERVICES_ARG: list(selection)}
    admitted = compiler.compile(extract_declared_tree(entry_over(catalog)), request)
    assert isinstance(admitted, AdmittedPlan), admitted
    return admitted, planned


@pytest.mark.proves("WR-ENV-12", "WR-ENV-12:identity-stable", "B", "B", "LOGIC", "CI")
@pytest.mark.proves("WR-ENV-12", "B2.1", "B", "B", "LOGIC", "CI")
def test_500_100_plan_identity_stable_across_runs(tmp_path: Path) -> None:
    gen = gen_catalog()
    first, second = gen.generate(N, SEED), gen.generate(N, SEED)
    assert first.catalog_text() == second.catalog_text()
    assert first.compose_text() == second.compose_text()
    selection = gen.select(SELECTED, SEED)
    assert len(selection) == SELECTED == len(set(selection))
    assert len(first.catalog["services"]) == N

    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    plan_a, closure_a = resolve(first, selection, tmp_path / "one")
    plan_b, closure_b = resolve(second, list(reversed(selection)), tmp_path / "two")
    assert plan_a.plan_digest == plan_b.plan_digest
    assert plan_a.declaration_digest == plan_b.declaration_digest
    assert closure_a == closure_b
    assert closure_a.definition_fingerprint == closure_b.definition_fingerprint
    # the closure holds the selection and stays inside its groups (bounded by the selection)
    assert set(selection) <= set(closure_a.services)
    assert len(closure_a.services) <= SELECTED * gen.GROUP
    # the identities are not constant: another seed rewires the definition (another fingerprint),
    # and a catalog one service larger is another declaration (another digest)
    _, rewired = resolve(gen.generate(N, SEED + 1), selection, tmp_path)
    assert rewired.definition_fingerprint != closure_a.definition_fingerprint
    wider, _ = resolve(gen.generate(N + 1, SEED), selection, tmp_path)
    assert wider.declaration_digest != plan_a.declaration_digest


@pytest.mark.proves("WR-ENV-12", "WR-ENV-12:answer-bounded", "B", "B", "MCP", "CI")
def test_answer_within_summary_budget(tmp_path: Path) -> None:
    """MCP host, fake binding: one `run` naming 100 of the 500 generated services is admitted
    and answered, and the answer the agent reads fits the run's summary budget with nothing
    pushed behind `detail`; it does not echo the selection back."""
    from twin import harness

    gen = gen_catalog()
    selection = gen.select(SELECTED, SEED)
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    shutil.copy(PLUGIN, home / "plugins" / PLUGIN.name)
    state = tmp_path / "engine.json"
    with harness.reference_host(home, harness.twin_environ(state)) as host:
        sent = host.request_count()
        answer = host.call(
            "run",
            {
                "plugin": PLUGIN.stem,
                "args": {schema.ENV_ARG: ENV, schema.SERVICES_ARG: selection},
                "wait_ms": tolerances.HARNESS_WAIT_MS,
                "completion": "terminal",
            },
        )
        assert host.request_count() == sent + 1
    assert isinstance(answer, dict) and answer.get("state") == "succeeded", answer
    assert answer["answer"]["outcome"] == "passed", answer
    body = json.dumps(answer["answer"], sort_keys=True, separators=(",", ":"))
    assert len(body.encode()) <= bounds.SUMMARY_BUDGET_DEFAULT, len(body)
    assert answer["answer"]["detail"] is None, answer["answer"]  # nothing overflowed
    assert not [s for s in selection if s in body], "the answer does not echo the selection"


def median_cost(synthetic: Any, selection: list[str], directory: Path) -> float:
    costs = []
    for _ in range(REPEATS):
        started = time.perf_counter()
        resolve(synthetic, selection, directory)
        costs.append(time.perf_counter() - started)
    return statistics.median(costs)


@pytest.mark.proves("WR-ENV-12", "WR-ENV-12:doubling-ratio", "B", "B", "LOGIC", "CI")
def test_resolution_cost_doubling_within_ratio(tmp_path: Path) -> None:
    gen = gen_catalog()
    selection = gen.select(SELECTED, SEED)
    small, large = gen.generate(N, SEED), gen.generate(2 * N, SEED)
    # the same request resolves to the same closure at N and at 2N: only the catalog doubled
    _, closure_n = resolve(small, selection, tmp_path)
    _, closure_2n = resolve(large, selection, tmp_path)
    assert closure_n.services == closure_2n.services
    cost_n = median_cost(small, selection, tmp_path)
    cost_2n = median_cost(large, selection, tmp_path)
    throughput_ratio = ((2 * N) / cost_2n) / (N / cost_n)
    assert throughput_ratio >= tolerances.append_cost_ratio(), (cost_n, cost_2n)
