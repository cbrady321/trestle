"""CI twins of `host/test_credential_currency.py` (L.RB-9.4; MC-B-03; STUB · CI): the same consumer
unit through the real loop (the tree rig) over `FakeContainerEngine` and `FakeGrant`, the join's
host scope read live from the same fake issuer (`consumers.LiveScope`). Same node names; only
`@stub-twin` labels."""

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

from twin import consumers

UNIT = "consumer.current"
SELECTOR = selector_name(Lineage("r_tree_0001", NodePath((UNIT,))))


class Engine(FakeContainerEngine):
    """The fake engine; `rotate` runs right after the consumer is created, and every effect on a
    container is recorded."""

    def __init__(self, rotate: Any = None) -> None:
        super().__init__()
        self.rotate = rotate
        self.effects: list[tuple[str, str]] = []

    def check(self, check: str, target: Any) -> CheckResult:
        if check == consumers.CONSUMER_IDENTITY:
            running = super().check("running", target)
            return CheckResult(running.satisfied, None, "the channel file is readable")
        return super().check(check, target)

    def create(self, spec: Any, ticket: Any) -> Any:
        self.effects.append(("create", selector_name(ticket.lineage)))
        answer = super().create(spec, ticket)
        if self.rotate is not None:
            self.rotate()
        return answer

    def restart(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("restart", target.selector))
        return super().restart(target, ticket)

    def recreate(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("recreate", target.selector))
        return super().recreate(target, ticket)


class Asked(FakeGrant):
    """The fake issuer, with every in-consumer authenticated call counted."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []
        self.delivered: list[str] = []

    def observe_in_consumer(self, consumer: Any) -> Any:
        self.asked.append(consumer.selector)
        return super().observe_in_consumer(consumer)

    def deliver(self, consumer: Any, ticket: Any) -> Any:
        self.delivered.append(consumer.selector)
        return super().deliver(consumer, ticket)


def run(tmp_path: Path, grant: Asked, engine: Engine) -> tuple[tk.TreeRig, dict[str, Any]]:
    rig = tk.tree_rig(
        tmp_path,
        tk.group("consumers", (tk.bind(UNIT),)),
        {UNIT: consumers.ConsumerUnit(UNIT, "consumer")},
        deadline_s=600,
        port_impl={
            ports.ResourceReads: engine,
            ports.ResourceCreate: engine,
            ports.ResourceOwned: engine,
            ports.GrantReads: grant,
            ports.GrantDelivery: grant,
        },
    )
    rig.run(consumers.LiveScope(grant, lambda: kit.NOW))
    return rig, rig.ends()[UNIT]


def test_readiness_requires_in_container_authenticated_call(tmp_path: Path) -> None:
    grant = Asked()
    grant.plant_consumer(SELECTOR)  # the channel holds the current credential
    rig, end = run(tmp_path, grant, Engine())
    assert end["condition"] == "satisfied", end
    assert SELECTOR in grant.asked and grant.delivered == []
    assert str(tk.answer_of(rig).outcome) == "passed"


@pytest.mark.stub_proven("WR-VERIFY-6:invalid-credential-not-ready@stub-twin")
def test_invalid_demo_credential_not_ready(tmp_path: Path) -> None:
    grant = Asked()
    grant.plant_consumer(SELECTOR, "never-issued")
    rig, end = run(tmp_path, grant, Engine())
    assert end["condition"] != "satisfied", end
    assert SELECTOR in grant.asked and grant.delivered == []
    assert str(tk.answer_of(rig).outcome) != "passed"


@pytest.mark.stub_proven("WR-ENV-13:refresh-in-place-container@stub-twin")
@pytest.mark.parametrize("consumer", ["container"])
def test_rotation_refreshed_in_place_no_recreate(tmp_path: Path, consumer: str) -> None:
    grant = Asked()
    grant.plant_consumer(SELECTOR)
    before = grant.current_generation()
    engine = Engine(rotate=grant.advance)
    rig, end = run(tmp_path, grant, engine)
    assert end["condition"] == "satisfied", end
    assert grant.current_generation() != before
    remedied = [
        r
        for r in rig.rows()
        if r.get("path") == UNIT and r["class"] == "issue" and r["effect"] == consumers.DELIVER
    ]
    assert len(remedied) == 1 and remedied[0]["remedy"]["code"] == codes.CREDENTIAL_STALE
    assert grant.delivered == [SELECTOR]
    assert grant.channel_generation(SELECTOR) == grant.current_generation()
    assert [e for e in engine.effects if e[0] in ("restart", "recreate")] == []
    assert grant.incarnation(SELECTOR) == 1  # the same consumer instance
