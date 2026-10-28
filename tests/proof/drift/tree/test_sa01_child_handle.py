"""SA-01 in A-2 (L.TR-2.2): a child handle is derived from (root run id, canonical path) alone
(V-1.1). Nothing else enters it (no clock, no counter, no random), so it is fixed at admission and
recomputed anywhere; and no forged handle is honoured: a path that is not a vertex, another root's
id, or a handle that does not derive from the pair reads as `projection.invalid_handle` (DM-28)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.fixtures.trees import generators
from tests.proof import harness
from tests.tree import runs
from trestle.common import codes
from trestle.common.plan import compiler
from trestle.common.types import RequestOutcome, RunView
from trestle.server import answer


def _plan(name: str) -> compiler.AdmittedPlan:
    compiled = compiler.compile(generators.declared_of(generators.fixture_tree(name).entry), {})
    assert isinstance(compiled, compiler.AdmittedPlan)
    return compiled


@pytest.mark.parametrize("sa", ["SA-01"])
def test_child_handle_derived_only_from_root_and_path(
    sa: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # derivation: a pure function of the pair (the same call, the same value; the root and the path
    # both move it; the digest is the one over the pair, spelled out here)
    for root in ("r_aaaaaaaa", "r_bbbbbbbb"):
        for path in (("data",), ("data", "db"), ("web",)):
            handle = answer.child_handle(root, path)
            expected = hashlib.sha256(f"{root}\0{'/'.join(path)}".encode()).hexdigest()[:24]
            assert (
                handle == f"{root}{answer.CHILD_SEP}{expected}" == answer.child_handle(root, path)
            )
            assert answer.root_run_id_of(handle) == root
    assert answer.child_handle("r_aaaaaaaa", ("web",)) != answer.child_handle(
        "r_bbbbbbbb", ("web",)
    )
    assert answer.child_handle("r_aaaaaaaa", ("web",)) != answer.child_handle(
        "r_aaaaaaaa", ("data",)
    )

    # the handle set of a plan is exactly one per non-root vertex, from the plan alone
    plan = _plan("three_level")
    handles = answer.child_paths("r_aaaaaaaa", plan)
    assert sorted(handles.values()) == sorted(
        tuple(v.path.split("/")) for v in plan.vertices if v.path
    )
    assert set(handles) == {answer.child_handle("r_aaaaaaaa", p) for p in handles.values()}

    # no forged handle is honoured on a real admitted root
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")  # a rewritten plugin is never stale
    (tmp_path / "plugins").mkdir()
    kernel = harness.fresh_kernel([tmp_path / "plugins"], home=tmp_path / "home")
    run = runs.admit(kernel, generators.fixture_tree("three_level"))
    other = runs.admit(kernel, generators.fixture_tree("three_level"))
    project = kernel.control.project
    genuine = answer.child_handle(run.run_id, ("data", "db"))
    assert isinstance(project.status(genuine), RunView)
    forged = {
        "path not a vertex": answer.child_handle(run.run_id, ("data", "ghost")),
        "root swapped for another real root": genuine.replace(run.run_id, other.run_id, 1),
        "root swapped for an unknown root": genuine.replace(run.run_id, "r_unknownroot", 1),
        "digest of another root's path": answer.child_handle(other.run_id, ("data", "db")).replace(
            other.run_id, run.run_id, 1
        ),
        "digest edited": genuine[:-1] + ("0" if genuine[-1] != "0" else "1"),
        "digest of a vertex, with garbage appended": genuine + "x",
    }
    for why, handle in forged.items():
        out = project.status(handle)
        assert isinstance(out, RequestOutcome) and out.code == codes.INVALID_HANDLE, why
    # a batch containing one fails as a whole (the existing await_runs rule)
    batch = kernel.control.await_runs([genuine, forged["path not a vertex"]], timeout_ms=0)
    assert isinstance(batch, RequestOutcome) and batch.code == codes.INVALID_HANDLE

    # the derivation is not carried anywhere on disk a request could supply: spec.json holds the
    # plan (the vertices), never a handle
    spec = (run.run_dir / "evidence" / "spec.json").read_text(encoding="utf-8")
    assert genuine not in spec and answer.CHILD_SEP not in json.loads(spec)["plan"]["root"]
