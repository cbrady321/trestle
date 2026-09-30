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
