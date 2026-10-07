"""The provision node never resubmits, and acceptance is not completion (L.RB-6.2; B3.3, WR-ENV-4).

The tree's own `ProvisionUnit` runs through the loop (MC-26's rig: the real child services and
lane under a manual clock) on the fake provisioning port, whose AUTHORITATIVE probe lags the
submit by `LAG` reads: the submit is accepted (APPLIED) but the record is not yet where the probe
looks, as on a replica. The step is not safe to resubmit (`Repeat.ONCE`), so the node polls within
its declared wait and issues no second submit; and the accepted submit is never reported as the
record being there."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.declarations import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    LoopFlags,
    Repeat,
)
from trestle_packs.fakes.provision import FakeProvision

from trestle_env import tree

LAG = 3  # authoritative reads that still miss the record after the submit is accepted


class LaggingProbe(FakeProvision):
    """The fake store whose authoritative probe (`observe`) lags a submit by `LAG` reads and whose
    convenience read (`check`) must never be consulted: completion is the probe's alone."""

    def __init__(self) -> None:
        super().__init__()
        self._missed: dict[str, int] = {}
        self.probes: list[bool] = []  # what each probe of this root's record answered

    def observe(self, spec: Any, lineage: Any, effect: str | None) -> Any:
        seen = super().observe(spec, lineage, effect)
        key = f"trwr-{lineage.root_run_id}-{'.'.join(lineage.path.segments)}"
        if seen.selector_present and self._missed.setdefault(key, 0) < LAG:
            self._missed[key] += 1  # accepted, but not visible to the probe yet
            seen = type(seen)(False, None, False, False, (), seen.found, None)
        self.probes.append(seen.selector_present)
        return seen

    def check(self, check: str, target: Any) -> Any:
        raise AssertionError("the convenience read is never a completion signal")


def rig_over(tmp_path: Path, store: FakeProvision) -> tk.TreeRig:
    root = AllDeclaration(
        unit="provisioning",
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=(ChildBinding(unit=tree.PROVISION_UNIT, params={}, needs=()),),
        concurrency=1,
        budget=timedelta(seconds=tree.ROOT_BUDGET_S),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=None,
    )
    return tk.tree_rig(
        tmp_path,
        root,
        {tree.PROVISION_UNIT: tree.ProvisionUnit()},
        port_impl={ports.ResourceReads: store, ports.ResourceCreate: store},
        deadline_s=tree.DEADLINE_S,
    )


def issues(rig: tk.TreeRig) -> list[dict[str, Any]]:
    return [r for r in rig.rows() if r.get("path") == tree.PROVISION_UNIT and r["class"] == "issue"]


@pytest.mark.proves("WR-ENV-4", "B3.3", "B", "B", "LOGIC", "CI")
def test_lagging_read_never_resubmits_nonresubmittable(tmp_path: Path) -> None:
    store = LaggingProbe()
    rig = rig_over(tmp_path, store)
    rig.run()
    assert rig.ends()[tree.PROVISION_UNIT]["condition"] == "satisfied"
    # the probe missed the accepted submit LAG times, and the node still submitted exactly once
    assert store.probes[-1] is True and store.probes.count(False) >= LAG
    assert store.submits == 1
    assert len(issues(rig)) == 1  # one ticket: no second submit was ever claimed
    # it polled on its declared wait, inside it, one interval per lagging read
    waits = rig.rig.cancel.waits
    assert len(waits) >= LAG
    assert set(waits) == {timedelta(seconds=tree.READY_POLL_S)}


@pytest.mark.proves("WR-ENV-4", "WR-ENV-4:completion-observed-separately", "B", "B", "LOGIC", "CI")
def test_acceptance_not_reported_as_completion(tmp_path: Path) -> None:
    store = LaggingProbe()
    rig = rig_over(tmp_path, store)
    rig.run()
    rows = rig.rows()
    accepted = next(
        n
        for n, r in enumerate(rows)
        if r.get("path") == tree.PROVISION_UNIT and r["class"] == "confirmation"
    )
    ended = next(
        n
        for n, r in enumerate(rows)
        if r.get("path") == tree.PROVISION_UNIT and r["class"] == "end"
    )
    assert accepted < ended
    # between acceptance and the node's end the probe was asked and answered "absent" LAG times:
    # the node was converging, not satisfied, until the authoritative read saw the record
    observed = [
        f["selector_present"]
        for kind, f in rig.rig.sink.events
        if kind == "step.observed" and f.get("path") == tree.PROVISION_UNIT
    ]
    after_acceptance = observed[observed.index(False) + 1 :]  # past the pre-submit absent read
    assert after_acceptance.count(False) >= LAG and after_acceptance[-1] is True
    assert rig.ends()[tree.PROVISION_UNIT]["condition"] == "satisfied"


def test_an_equivalent_record_already_in_the_store_is_reused_with_no_submit(tmp_path: Path) -> None:
    store = FakeProvision()
    store.plant_found(tree.PROVISION_SERVICE)  # an earlier equivalent run's record
    rig = rig_over(tmp_path, store)
    rig.run()
    assert rig.ends()[tree.PROVISION_UNIT]["condition"] == "satisfied"
    assert store.submits == 0 and issues(rig) == []
    answer = tk.answer_of(rig)
    shown = {"/".join(n.path): n.disposition for n in [answer.primary, *answer.listed] if n.path}
    assert str(shown[tree.PROVISION_UNIT]) == "reused"
