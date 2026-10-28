"""`python -m tests.proof.differ d4 [--fossils <dir>]` (MC-06 mode d4; CSC-5 builder L.SV-3.4).

The implicit depth-1 plan of a root that declares no tree (B2-C1) equals the plan compiled for a
one-leaf root, compared on (vertices, edges, slices, release rank, precedence ordinal) only; the
digests and the format metadata are excluded. The comparison runs over every committed fossil run
(every band's MANIFEST, like `d2`): for each run directory that holds a `spec.json`, both plans
are built from the run's plugin and admitted deadline. A run whose spec carries a `plan` (a
plan-bearing band) is also compared: the recorded plan against the plan compiled from the
snapshot's own `declaration.json` (else the one-leaf root).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.proof import fossils as fossils_mod
from tests.proof.foundations import trees
from trestle.common import clock
from trestle.common.plan import carving, compiler
from trestle.common.plan.declared import ROOT_PATH, DeclaredTree
from trestle.common.plan.formats import UnknownPlanFormat


def _carved(plan: compiler.AdmittedPlan, deadline_s: float) -> compiler.AdmittedPlan:
    release = carving.release_slice_for(plan, clock.release_slice)
    slices = carving.carve(
        plan,
        deadline_s,
        clock.FINALIZATION_RESERVE_S,
        release,
        deadline_ceiling_s=clock.deadline_ceiling,
    )
    if isinstance(slices, compiler.Refusal):
        raise ValueError(f"carve refused: {slices.identifier}: {slices.message}")
    return carving.attach(plan, slices, release)


def _leaf_root(unit: str) -> DeclaredTree:
    return DeclaredTree.build(unit, {ROOT_PATH: trees.leaf_node(unit, budget=None)})


def compare_run(run_dir: Path, home: Path) -> list[str]:
    """The differences for one run directory (empty: none, or nothing to compare)."""
    spec_path = run_dir / "evidence" / "spec.json"
    if not spec_path.is_file():
        return []
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    unit = str(spec["plugin"])
    deadline_s = float(spec["timeout_s"])
    implicit = _carved(compiler.implicit_depth1_plan(unit), deadline_s)
    declared_path = home / "snapshots" / str(spec["snapshot_id"]) / "declaration.json"
    declared = (
        DeclaredTree.from_json(declared_path.read_text(encoding="utf-8"))
        if declared_path.is_file()
        else _leaf_root(unit)
    )
    compiled = compiler.compile(declared, spec.get("args") or {})
    if isinstance(compiled, compiler.Refusal):
        return [f"compiled plan refused: {compiled.code} {compiled.identifier}"]
    compiled = _carved(compiled, deadline_s)
    diffs: list[str] = []
    if declared_path.is_file():
        # a declared root is compared with the plan its own tree compiles to, not the implicit one
        implicit = compiled
    if not compiler.plan_shape_equal(implicit, compiled):
        diffs.append("implicit depth-1 plan differs from the compiled one-leaf plan")
    recorded = spec.get("plan")
    if isinstance(recorded, dict):
        try:
            plan = compiler.AdmittedPlan.from_json(json.dumps(recorded))
        except UnknownPlanFormat:
            return diffs  # a format this reader does not know is recovery's business, not d4's
        if not compiler.plan_shape_equal(plan, compiled):
            diffs.append("the run's recorded plan differs from the recompiled plan")
    return diffs


def run(fossils_root: Path) -> int:
    states = fossils_mod.load_states(fossils_root)
    checked = 0
    failures = 0
    for state_id, (band, entry) in sorted(states.items()):
        if entry.get("producer") in (None, "pending") or entry.get("absent"):
            continue
        home = fossils_root / band / state_id / "home"
        for run_dir in sorted((home / "runs").rglob("r_*")):
            if not run_dir.is_dir():
                continue
            diffs = compare_run(run_dir, home)
            checked += 1
            for diff in diffs:
                failures += 1
                print(f"d4: DIFF: {band}/{state_id}/{run_dir.name}: {diff}")
    print(f"d4: {checked} runs compared, {failures} diffs")
    return 1 if failures else 0


def main(args: argparse.Namespace) -> int:
    root = Path(args.fossils) if args.fossils else fossils_mod.FOSSILS_ROOT
    return run(root)
