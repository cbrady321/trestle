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
from trestle_packs.process.local import LocalProcessPort

from twin import consumers, local_app, local_consumer

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


CONTAINER = pytest.param(
    "container", marks=pytest.mark.stub_proven("WR-ENV-13:refresh-in-place-container@stub-twin")
)
LOCAL_APP = pytest.param(
    "local_app", marks=pytest.mark.stub_proven("WR-ENV-13:refresh-in-place-local-app@stub-twin")
)


@pytest.mark.parametrize("consumer", [CONTAINER, LOCAL_APP])
def test_rotation_refreshed_in_place_no_recreate(tmp_path: Path, consumer: str) -> None:
    if consumer == "local_app":
        return _rotation_local_app(tmp_path)
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


def _rotation_local_app(tmp_path: Path) -> None:
    """The consumer is a real local process (the stdlib app), the issuer and its delivery fakes:
    the product's `ChannelDelivery` cannot reach a local process (`twin/local_consumer.py`)."""
    run_id = "r_tree_0001"
    selector = local_consumer.selector_for(run_id)
    grant = Asked()
    grant.plant_consumer(selector)  # the app's credentials file holds the current generation
    before = grant.current_generation()
    log = tmp_path / "app-events.log"
    command = local_consumer.app_command(local_app.free_port(), log)
    # start-up is the harness's to wait for (`local_app.AwaitListening`): unwaited, a fast run
    # stops the app before its interpreter has written `listening` (seen on Linux CI)
    launcher = local_app.AwaitListening(LocalProcessPort(), log)
    watched = local_consumer.Watched(launcher, selector, rotate=grant.advance)
    rig = tk.tree_rig(
        tmp_path,
        tk.group("consumers", (tk.bind(UNIT),)),
        {UNIT: local_consumer.LocalConsumerUnit(command)},
        deadline_s=600,
        run_id=run_id,
        port_impl=local_consumer.port_map(watched, grant, grant),
    )
    rig.run(consumers.LiveScope(grant, lambda: kit.NOW))
    assert rig.ends()[UNIT]["condition"] == "satisfied"
    assert grant.current_generation() != before  # the host rotated while the run waited
    assert len(local_consumer.stale_remedy_rows(rig.rows())) == 1
    assert grant.delivered == [selector]
    assert grant.channel_generation(selector) == grant.current_generation()
    assert watched.repairs == []  # no restart, no recreate
    assert len(set(watched.seen)) == 1 and len(watched.seen) >= 2  # the very same process
    assert log.read_text().splitlines()[:1] == ["listening"]  # started once, before any stop
    assert log.read_text().splitlines().count("listening") == 1
