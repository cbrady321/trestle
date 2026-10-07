"""L.TR-1.4: the tree's lease set is decided once at admission, from the root.

Where the root declares an environment the plan's `lease_set` is that one key for the whole tree
(WR-UNIT-4): a child declaring another is refused `admission.lease_set_undecidable` naming the
child, before any run id and before any lease queue entry, an undeclared child runs under the
root key, and the plan carries no per-vertex acquire, queue or release. A root that declares no
environment is neither refused nor given a lease from its children (nothing is claimed for that
shape: OQ-29 / OQ-31 are neutral). The lease stays a definition over durable state, never a
store: admission refusals leave the holders and the scheduler untouched."""

from __future__ import annotations

import pytest

from tests.tree.test_tr1_admission import admitted, edited, publish, refused, run_dirs, source
from trestle.common import codes
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.plan.declared import canonical_json
from trestle.common.types import AdmitRequest
from trestle.server import pool as pools
from trestle.server.admission import plan_for_admission
from trestle.server.main import Kernel

DEADLINE_S = 300.0
SECOND = '"second": leaf("second", env="env_b"),'

proves_mismatch = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:env-mismatch-refused", "A", "tree", "PROC+LOGIC", "CI"
)
proves_no_verb = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:no-child-lease-verb-in-plan", "A", "tree", "PROC+LOGIC", "CI"
)


def plan_of(kernel: Kernel, plugin: str, args: dict[str, object]) -> object:
    snap = kernel.registry.get(plugin)
    assert snap is not None
    return plan_for_admission(snap, AdmitRequest(plugin=plugin, args=args), DEADLINE_S)


def untouched(kernel: Kernel) -> None:
    """The refusal reached no lease state: no holder, nothing queued or waiting."""
    assert pools.key_runs(pools.load_sched(kernel.home)) == []
    assert not kernel.control.scheduler.queue and not kernel.control.scheduler.waiting
    assert run_dirs(kernel) == []


@proves_mismatch
def test_child_env_mismatch_refused_before_run_id(tree_kernel: Kernel) -> None:
    """`lease_pair`: the root and `first` project `env`, `second` projects `env_b`. Called with
    two values (E, E') the second child's key differs from the root's."""
    name = publish(tree_kernel, source("lease_pair"))
    outcome = refused(tree_kernel, name, {"env": "dev", "env_b": "staging"})
    assert outcome.code == codes.LEASE_SET_UNDECIDABLE == "admission.lease_set_undecidable"
    assert "second" in outcome.message, outcome.message  # names the child, not the root
    untouched(tree_kernel)
    # the same tree with one value (E, E) is valid: it is admitted (L.TR-L.1) and holds the one
    # lease key of the whole tree
    result = admitted(tree_kernel, name, {"env": "dev", "env_b": "dev"})
    assert pools.key_runs(pools.load_sched(tree_kernel.home)) == [
        (result.run_id, canonical_json("dev"))
    ]


@proves_mismatch
def test_undeclared_child_runs_under_root_key(tree_kernel: Kernel) -> None:
    """A child that declares no environment adds nothing to the key: it runs under the root's, so
    its own argument may hold any value."""
    undeclared = edited("lease_pair", SECOND, '"second": leaf("second"),')
    name = publish(tree_kernel, undeclared)
    plan = plan_of(tree_kernel, name, {"env": "dev", "env_b": "anything"})
    assert isinstance(plan, AdmittedPlan), plan
    assert plan.lease_set == (canonical_json("dev"),)
    admitted(tree_kernel, name, {"env": "dev", "env_b": "anything"})  # valid: admitted (L.TR-L.1)


@proves_no_verb
def test_plan_has_no_child_lease_entry(tree_kernel: Kernel) -> None:
    """One decision for the whole tree: the plan's `lease_set` is one key and no vertex carries
    an acquire, a queue entry or a release of its own."""
    name = publish(tree_kernel, source("lease_pair"))
    plan = plan_of(tree_kernel, name, {"env": "dev", "env_b": "dev"})
    assert isinstance(plan, AdmittedPlan), plan
    assert len(plan.vertices) == 3 and plan.lease_set == (canonical_json("dev"),)
    body = plan.body()
    assert [key for key in body if "lease" in key.split("_")] == ["lease_set"]
    for vertex in plan.vertices:
        words = {word for key in vertex.to_dict() for word in key.split("_")}
        assert not words & {"lease", "acquire", "queue"}


@proves_mismatch
def test_root_without_env_not_refused_on_child_env(tree_kernel: Kernel) -> None:
    """`lease_root_undeclared`: the root declares no environment and the child declares `env`. It
    is not refused (it compiles and is admitted, L.TR-L.1), and it holds no lease."""
    name = publish(tree_kernel, source("lease_root_undeclared"))
    plan = plan_of(tree_kernel, name, {"env": "dev"})
    assert isinstance(plan, AdmittedPlan), plan
    assert plan.lease_set == ()
    admitted(tree_kernel, name, {"env": "dev"})
    assert pools.key_runs(pools.load_sched(tree_kernel.home)) == []
