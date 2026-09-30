"""Tree run directories for the in-library TR-2 proofs (A2c6-1, MC-26).

No tree is admissible before TR-L (MC-B3-03) and the composite walk lands at `L.TR-3.2`, so a
TR-2 test acts as the loop: it admits a tree through the harness (`admit_tree`, the post-refusal
half of admission), then writes the lane through MC-19's `AttemptLane` surface (B2-C7). The
plugin function of every tree fixture returns at once, so driving such a run finalizes it over
the planted lane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tests.fixtures.trees import generators
from tests.proof import harness
from trestle.child.attempt_lane import AttemptLane
from trestle.common import lane_format as lf
from trestle.common.plan.compiler import AdmittedPlan
from trestle.server import fold
from trestle.server.ledger import evidence_dir
from trestle.server.main import Kernel

AT = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class TreeRun:
    """An admitted, not yet driven tree run and the plan its spec carries."""

    admitted: harness.AdmittedTree
    plan: AdmittedPlan

    @property
    def kernel(self) -> Kernel:
        return self.admitted.kernel

    @property
    def run_id(self) -> str:
        return self.admitted.run_id

    @property
    def run_dir(self) -> Path:
        return self.admitted.run_dir

    def paths(self) -> list[tuple[str, ...]]:
        """Every vertex of the plan, canonical order, as lane paths (the root is `()`)."""
        return [tuple(v.path.split("/")) if v.path else () for v in self.plan.vertices]

    def leaves(self) -> list[tuple[str, ...]]:
        parents = {p[:-1] for p in self.paths() if p}
        return [p for p in self.paths() if p and p not in parents]


def admit(kernel: Kernel, tree: generators.Tree) -> TreeRun:
    """Publish `tree` into the kernel's plugin directory and admit a run of it."""
    plugin = tree.write(kernel.registry.plugin_dirs[0])
    admitted = harness.admit_tree(plugin, kernel=kernel)
    spec = json.loads((evidence_dir(admitted.run_dir) / "spec.json").read_text(encoding="utf-8"))
    plan = fold.plan_of_spec(spec)
    assert plan is not None and len(plan.vertices) == tree.vertices
    return TreeRun(admitted, plan)


def lane_of(run: TreeRun, *, record_plan: bool = True) -> AttemptLane:
    """The run's attempt lane over its admitted scope and `lane_entries` (V-13), plan recorded."""
    lane = AttemptLane(run.run_dir, run.plan.lane_entries, run.plan.selected_scope)
    if record_plan:
        lane.record_plan(
            lf.PlanIdentity(
                declaration_digest=run.plan.declaration_digest or "",
                args_hash="a" * 64,
                selection={},
                observations_digest="o" * 64,
            )
        )
    return lane


def end(
    run: TreeRun,
    path: tuple[str, ...],
    condition: lf.Condition | None = lf.Condition.SATISFIED,
    *,
    code: str | None = None,
    human_action: str | None = None,
    cut: lf.Cut | None = None,
    provenance: lf.Provenance | None = lf.Provenance.CREATED,
) -> lf.NodeEnd:
    """One vertex's `NodeEnd` as the loop writes it (B1-C11): a composite that ended normally
    has `condition` `None`."""
    resend = lf.Resend.SUCCEEDS_AFTER_ACTION if human_action else None
    return lf.NodeEnd(
        lineage=lf.Lineage(run.run_dir.name, path),
        at=AT,
        condition=condition,
        code=code,
        human_action=human_action,
        resend=resend,
        provenance=provenance if cut is None else None,
        cut=cut,
    )


def write_all_ends(run: TreeRun, lane: AttemptLane) -> None:
    """Every vertex ends: each leaf satisfied, each composite `None` (rolled up)."""
    leaves = set(run.leaves())
    for path in run.paths():
        if path in leaves:
            lane.record_end(end(run, path))
        else:
            lane.record_end(end(run, path, None, provenance=None))
