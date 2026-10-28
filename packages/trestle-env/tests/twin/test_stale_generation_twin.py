"""L.RB-9.5 twins: an older credential generation on the fake binding (STUB · CI).

The consumer tree of `twin/consumers.py` runs through the real loop (the tree rig: the real lane
and services under a manual clock) over `FakeContainerEngine` and `FakeGrant`, with the join's host
scope read by the loop from the same fake issuer (the bound `DemoHostScope`). The same
node names as `host/test_stale_generation.py`; only `@stub-twin` labels.

`test_owned_process_older_generation_restarted` (L.RB-9.5.fix1) runs a real local process (the
stdlib app, the real `LocalProcessPort`) that takes its credential at start, read through the fake
issuer: `twin/local_consumer.py`'s `stale_restart_case`, shared with the HOST node.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.workflow import codes, ports
from trestle.workflow.values import CheckResult, Lineage, NodePath
from trestle_packs.fakes.container import FakeContainerEngine, selector_name
from trestle_packs.fakes.grant import FakeGrant
from trestle_packs.grant.host_scope import DemoHostScope

from twin import consumers, local_consumer

UNIT = "consumer.stale"
FOUND = "trestle-stale-consumer"


class ConsumerEngine(FakeContainerEngine):
    """The fake engine; a running container answers the consumer identity check."""

    def __init__(self) -> None:
        super().__init__()
        self.effects: list[tuple[str, str]] = []

    def check(self, check: str, target: Any) -> CheckResult:
        if check == consumers.CONSUMER_IDENTITY:
            running = super().check("running", target)
            return CheckResult(running.satisfied, None, "the channel file is readable")
        return super().check(check, target)

    def create(self, spec: Any, ticket: Any) -> Any:
        self.effects.append(("create", selector_name(ticket.lineage)))
        return super().create(spec, ticket)

    def stop(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("stop", target.selector))
        return super().stop(target, ticket)

    def restart(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("restart", target.selector))
        return super().restart(target, ticket)

    def recreate(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("recreate", target.selector))
        return super().recreate(target, ticket)


class Delivery:
    """The fake issuer's delivery, with every re-delivery recorded by the consumer it served."""

    def __init__(self, grant: FakeGrant) -> None:
        self.grant, self.delivered = grant, []  # type: ignore[var-annotated]

    def release_descriptor(self, call: Any) -> Any:
        return self.grant.release_descriptor(call)

    def deliver(self, consumer: Any, ticket: Any) -> Any:
        self.delivered.append(consumer.selector)
        return self.grant.deliver(consumer, ticket)


def _rig(tmp_path: Path, system: str) -> tuple[tk.TreeRig, ConsumerEngine, FakeGrant, Delivery]:
    engine, grant = ConsumerEngine(), FakeGrant()
    delivery = Delivery(grant)
    root = tk.group("consumers", (tk.bind(UNIT),))
    rig = tk.tree_rig(
        tmp_path,
        root,
        {UNIT: consumers.ConsumerUnit(UNIT, system)},
        deadline_s=600,
        port_impl={
            ports.ResourceReads: engine,
            ports.ResourceCreate: engine,
            ports.ResourceOwned: engine,
            ports.GrantReads: grant,
            ports.HostScopeReads: DemoHostScope(grant, now=lambda: kit.NOW),
            ports.GrantDelivery: delivery,
        },
    )
    return rig, engine, grant, delivery


def _stale(grant: FakeGrant, selector: str) -> str:
    """The consumer holds the issuer's current generation, then the issuer moves on."""
    old = grant.current_generation()
    grant.advance()
    grant.plant_consumer(selector, old)
    return old


def test_owned_container_older_generation_recreated(tmp_path: Path) -> None:
    rig, engine, grant, delivery = _rig(tmp_path, "consumer")
    selector = selector_name(Lineage("r_tree_0001", NodePath((UNIT,))))
    _stale(grant, selector)
    rig.run()
    end = rig.ends()[UNIT]
    assert end["condition"] == "satisfied", end
    issues = [r for r in rig.rows() if r.get("path") == UNIT and r["class"] == "issue"]
    remedied = [r for r in issues if r["effect"] == consumers.DELIVER]
    assert len(remedied) == 1 and remedied[0]["remedy"]["code"] == codes.CREDENTIAL_STALE
    assert delivery.delivered == [selector]  # re-delivered on the same handle
    assert grant.channel_generation(selector) == grant.current_generation()
    assert [e for e in engine.effects if e[0] in ("restart", "recreate")] == []  # a repair
    answer = tk.answer_of(rig)
    assert str(answer.primary.node_class) == "repaired", answer  # passed, with a repair


@pytest.mark.stub_proven("WR-OWN-9:not-owned-blocked-untouched@stub-twin")
def test_found_container_unproven_generation_blocked_untouched(tmp_path: Path) -> None:
    rig, engine, grant, delivery = _rig(tmp_path, FOUND)
    engine.plant_found(FOUND)
    old = _stale(grant, FOUND)
    rig.run()
    end = rig.ends()[UNIT]
    assert (end["condition"], end["code"]) == ("incompatible", codes.CREDENTIAL_STALE), end
    answer = tk.answer_of(rig)
    assert str(answer.outcome) == "blocked" and answer.primary.human_action
    assert [e for e in engine.effects if e[1] == FOUND] == [] and delivery.delivered == []
    assert grant.channel_generation(FOUND) == old  # untouched
    assert FOUND in engine.inventory()["containers"]


@pytest.mark.stub_proven("WR-OWN-9:owned-process-restarted@stub-twin")
def test_owned_process_older_generation_restarted(tmp_path: Path) -> None:
    grant = FakeGrant()
    delivery = Delivery(grant)
    before = grant.current_generation()
    case = local_consumer.stale_restart_case(
        tmp_path, "r_tree_0001", grant, delivery, issue=grant.plant_consumer, rotate=grant.advance
    )
    local_consumer.assert_restarted(case)
    assert grant.current_generation() != before  # the issuer moved on after the first launch
    assert delivery.delivered == []  # nothing was delivered: the restart took the new one
    assert grant.channel_generation(case.selector) == grant.current_generation()
