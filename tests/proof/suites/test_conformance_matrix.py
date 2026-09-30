"""MC-35 acceptance (L.SV-5.14): every registered Slice A workflow publishes and admits.

Each entry of `SLICE_A_WORKFLOWS` is written to a scratch plugin directory, published by a real
kernel and admitted through `Admission.admit` (the path the MCP `run` tool wraps), which writes the
run through `write_admitted_run`: with `TRESTLE_ADMISSION_AUDIT` set, that admission is recorded
under this node's id, which is what J-SINGLE (b)'s vacuity guard for the conformance suites reads.
Nothing here starts a run: admission only (the run's behaviour is the spine suites' business)."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from tests.proof.suites import guarantees
from tests.proof.suites.workflows import ENTRY_KEYS, SLICE_A_WORKFLOWS, fixture_file
from tests.single.control import support
from trestle.common.types import AdmitRequest, AdmitResultAdmitted
from trestle.workflow.declarations import LeafDeclaration


def _declared_codes(path: Path) -> tuple[str, ...]:
    """The stable codes the fixture's declaration names, read from its `DECLARATION`."""
    spec = importlib.util.spec_from_file_location(f"_mc35_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    decl = module.DECLARATION
    assert isinstance(decl, LeafDeclaration)
    remedy_codes = {r.code for r in decl.remedies}
    return tuple(sorted(set(decl.retryable) | remedy_codes))


def _env_arg(source: str) -> str | None:
    """The `env_arg=` keyword of the source's one `@trestle(...)` decorator, read statically."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "trestle":
            for kw in node.keywords:
                if kw.arg == "env_arg" and isinstance(kw.value, ast.Constant):
                    return str(kw.value.value)
    return None


def registry_problems(registry: dict[str, dict[str, Any]]) -> list[str]:
    """What is wrong with a registry table: its shape, its fixture files, and any field that has
    drifted from what the fixture itself declares."""
    problems: list[str] = []
    if not registry:
        problems.append("the registry is empty")
    for name, entry in registry.items():
        if set(entry) != ENTRY_KEYS:
            problems.append(f"{name}: keys {sorted(entry)} are not {sorted(ENTRY_KEYS)}")
            continue
        path = fixture_file(entry)
        if not path.is_file():
            problems.append(f"{name}: {entry['fixture_path']} is missing")
            continue
        if Path(str(entry["fixture_path"])).stem != name:
            problems.append(f"{name}: the fixture file is not named for the plugin")
        if not isinstance(entry["oq31_eligible_both"], bool):
            problems.append(f"{name}: oq31_eligible_both is not a bool")
        if _env_arg(path.read_text(encoding="utf-8")) != entry["env_arg"]:
            problems.append(f"{name}: env_arg differs from the plugin's @trestle env_arg")
        if tuple(entry["declared_codes"]) != _declared_codes(path):
            problems.append(f"{name}: declared_codes differ from the declaration's")
    return problems


def test_registry_is_well_formed() -> None:
    assert "spine_leaf" in SLICE_A_WORKFLOWS
    assert registry_problems(SLICE_A_WORKFLOWS) == []


def test_second_domain_free_is_registered_and_is_a_different_domain() -> None:
    """L.SL-11.1: the second workflow is registered, is not the spine fixture, and differs from it
    where a runtime that quietly special-cased the spine fixture would show: its completion source
    (recorded, not observed) and its one effect (an event, not a create)."""
    from trestle.workflow.declarations import CompletionSource, EffectFacetClass

    assert set(SLICE_A_WORKFLOWS) >= {"spine_leaf", "second_domain_free"}
    modules = {}
    for name in ("spine_leaf", "second_domain_free"):
        path = fixture_file(SLICE_A_WORKFLOWS[name])
        spec = importlib.util.spec_from_file_location(f"_mc35_domain_{name}", path)
        assert spec is not None and spec.loader is not None
        modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modules[name])
    spine, second = modules["spine_leaf"].DECLARATION, modules["second_domain_free"].DECLARATION
    assert spine.flags.completion is CompletionSource.OBSERVED
    assert second.flags.completion is CompletionSource.RECORDED
    assert {e.facet for e in second.effects} == {EffectFacetClass.EVENT}
    assert EffectFacetClass.EVENT not in {e.facet for e in spine.effects}
    tree = ast.parse(fixture_file(SLICE_A_WORKFLOWS["second_domain_free"]).read_text("utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    for module in imported:  # no Docker, toolchain adapter or AWS: only stdlib, the API, the fakes
        assert not any(w in module.lower() for w in ("docker", "toolchain", "boto", "aws")), module
        assert module.split(".")[0] in {
            "__future__",
            "datetime",
            "typing",
            "trestle",
            "trestle_packs",
        }
    assert "trestle_packs.fakes" in imported and not any(
        m.startswith("trestle_packs.") and m != "trestle_packs.fakes" for m in imported
    )


def test_planted_registry_defects_are_caught() -> None:
    real = SLICE_A_WORKFLOWS["spine_leaf"]
    planted = {
        "gone": dict(real, fixture_path="tests/fixtures/workflows/gone.py"),
        "spine_leaf": dict(real, env_arg="other", declared_codes=("x.y",)),
        "short": {"fixture_path": real["fixture_path"]},
    }
    problems = registry_problems(planted)
    assert len(problems) == 4, problems
    assert registry_problems({}) == ["the registry is empty"]


def test_registry_entries_publish_and_admit(tmp_path: Path) -> None:
    """Every entry publishes (the registry holds a snapshot) and admits (one vertex, a run
    directory whose spec carries the plan): the audit records each under this node."""
    for name, entry in SLICE_A_WORKFLOWS.items():
        source = fixture_file(entry).read_text(encoding="utf-8")
        kernel = support.make_kernel(tmp_path / name, {name: source})
        kernel.registry.maybe_refresh()
        snap = kernel.registry.get(name)
        assert snap is not None, f"{name} was not published"
        args: dict[str, object] = {}
        if entry["env_arg"] is not None:
            args[str(entry["env_arg"])] = "dev"
        result = kernel.control.admission.admit(AdmitRequest(plugin=name, args=args))
        assert isinstance(result, AdmitResultAdmitted), (name, result)
        spec = json.loads(
            (support.run_dir_of(kernel, result.run_id) / "evidence" / "spec.json").read_text(
                encoding="utf-8"
            )
        )
        assert len(spec["plan"]["vertices"]) == 1, name


# ---- the guarantee suites, parameterized over MC-35 (L.SL-11.1)


@pytest.mark.proves("WR-PLAN-11", "A2.3", "A", "single", "LOGIC+PROC", "CI")
def test_second_workflow_passes_every_guarantee_suite(tmp_path: Path) -> None:
    """`second_domain_free` passes every guarantee suite that the spine fixture passes, with no
    runtime change: every suite runs, every suite is green, and the suites are the shared set."""
    results = guarantees.run_all(
        "second_domain_free", SLICE_A_WORKFLOWS["second_domain_free"], tmp_path
    )
    assert set(results) == set(guarantees.GUARANTEE_SUITES)
    assert {suite: problems for suite, problems in results.items() if problems} == {}


@pytest.mark.parametrize("name", sorted(SLICE_A_WORKFLOWS))
def test_every_registered_workflow_passes_every_guarantee_suite(name: str, tmp_path: Path) -> None:
    """The same suites over every MC-35 entry (the second workflow is one of them; this is the
    parameterization TR-6 extends with the tree workflows)."""
    results = guarantees.run_all(name, SLICE_A_WORKFLOWS[name], tmp_path)
    assert {suite: problems for suite, problems in results.items() if problems} == {}


def _observed_copy(**changes: Any) -> guarantees.Observed:
    """An `Observed` shaped like a green run, with `changes` planted."""
    from tests.proof import records

    answer = {key: None for key in guarantees.DECISIVE} | {
        "outcome": "passed",
        "primary": {"path": []},
        "cleanup": {"unknown": 0},
    }
    reply = {"run_id": "r_planted_0001", "state": "succeeded", "answer": answer}
    base: dict[str, Any] = {
        "name": "planted",
        "reply": reply,
        "resent": dict(reply),
        "read": {"result": [dict(reply)]},
        "run_dirs": (Path("/x/r_planted_0001"),),
        "lane": records.LaneRows(),
        "survivors": (),
    }
    return guarantees.Observed(**(base | changes))


def test_planted_defects_are_caught_by_the_suite_that_owns_them() -> None:
    """Each suite fails on the defect it exists for and is green on a clean record (so a suite
    that never fails would be seen); the lane suite is vacuous, never green, on an empty lane."""
    suites = guarantees.GUARANTEE_SUITES
    clean = _observed_copy()
    assert suites["one_call_terminal_answer"](clean) == []
    assert suites["default_run_passes"](clean) == []
    assert suites["identical_resend_joins_one_execution"](clean) == []
    assert suites["read_matches_answer"](clean) == []
    assert suites["no_process_left"](clean) == []
    lane = suites["lane_record_facts"](clean)
    assert lane and "vacuous" in lane[0]

    reply = clean.reply
    no_class = dict(reply, answer=dict(reply["answer"], outcome="succeeded"))
    assert suites["one_call_terminal_answer"](_observed_copy(reply=no_class))
    failed = dict(reply, answer=dict(reply["answer"], outcome="failed"))
    assert suites["default_run_passes"](_observed_copy(reply=failed))
    no_cleanup = dict(reply, answer={k: v for k, v in reply["answer"].items() if k != "cleanup"})
    assert suites["one_call_terminal_answer"](_observed_copy(reply=no_cleanup))
    other_run = dict(reply, run_id="r_planted_0002")
    assert suites["identical_resend_joins_one_execution"](_observed_copy(resent=other_run))
    assert suites["identical_resend_joins_one_execution"](
        _observed_copy(run_dirs=(Path("/x/a"), Path("/x/b")))
    )
    drifted = {"result": [dict(reply, answer=dict(reply["answer"], recovered=True))]}
    assert suites["read_matches_answer"](_observed_copy(read=drifted))
    assert suites["no_process_left"](
        _observed_copy(survivors=("python -m trestle.child.main r_p",))
    )
