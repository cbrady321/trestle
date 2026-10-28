"""A credential consumer that is a LOCAL app (L.RB-9.4.fix1; WR-ENV-13:refresh-in-place-local-app;
not a test module).

The product `ChannelDelivery` refreshes a local app in place: its channel directory is the one the
owned process's `proc-<16 hex>` selector names (`channel_directory`), and the app is told where it
is through `TRESTLE_CHANNEL_DIR` (`channel_env`). The test-defined `LocalAppDelivery` that stood in
for it (B-HOST2-RETURN's deviation) is gone (V03 stage 10, decision D).

`LocalConsumerUnit` is L.RB-9.5's `ConsumerUnit` over an agent-launched spec (a real
`LocalProcessPort` process, the stdlib app in `never` mode: it only has to stay alive), declared
with resource kind `local_process`. Shared by `host/test_credential_currency.py` and its twin.

The owned process restarted on an older generation (L.RB-9.5.fix1; WR-OWN-9:owned-process-restarted;
shared by `host/test_stale_generation.py` and its twin): `RestartingConsumerUnit` is a local app
that takes its credential when it starts (`LaunchCredential` puts the issuer's current one where the
app reads it, on every launch), so nothing can refresh it in place: it declares no delivery, and its
`CREDENTIAL_STALE` remedy is one `restart` of the run's own handle (a repair on the same handle,
B3-C5, WR-UNIT-5, never a release, a recreate or a new create). `stale_restart_case` runs it: the
issuer rotates right after the launch, the join proves the app's generation older, the remedy
restarts it, and the relaunched process holds the current generation.
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.workflow import EffectDeclaration, EffectFacetClass, Lifetime, codes, ports
from trestle.workflow.declarations import LeafDeclaration, RealizationKind, RemedyDeclaration
from trestle.workflow.ports import BoundCommand, Resolved, ResourceSpec
from trestle.workflow.units import ActContext, Acted, EffectFacets, Step
from trestle.workflow.values import (
    Lineage,
    NodePath,
    Verdict,
)
from trestle_packs.grant import CHANNEL_FILE
from trestle_packs.grant.host_scope import DemoHostScope
from trestle_packs.process.local import LocalProcessPort, run_scoped_selector

from trestle_env import tree
from twin import consumers
from twin.local_app import APP

UNIT: Final = "consumer.current"
STALE_UNIT: Final = "consumer.stale"
RESTART: Final = "restart"


def selector_for(run_id: str, unit: str = UNIT) -> str:
    """The owned consumer's selector, known before the run (it depends on `(lineage, effect)`)."""
    return run_scoped_selector(Lineage(run_id, NodePath((unit,))), consumers.UP)


def app_command(log: Path) -> BoundCommand:
    return BoundCommand(
        "consumer",
        (sys.executable, str(APP), "never"),
        {"PORT": "0", "APP_EVENT_LOG": str(log), "PATH": "/usr/bin:/bin"},
        Resolved(sys.executable, "3.12", "pin", "adoption"),
        False,
    )


class LocalConsumerUnit(consumers.ConsumerUnit):
    """The credential consumer, a local process this run launches."""

    def __init__(self, command: BoundCommand, unit: str = UNIT) -> None:
        super().__init__(unit, "consumer")
        self._spec = ResourceSpec(
            "consumer", RealizationKind.AGENT_LAUNCHED_PROJECT, consumers.CONSUMER_ENTRY, command
        )

    def declare(self) -> LeafDeclaration:
        kind = tree.RESOURCE_KINDS[RealizationKind.AGENT_LAUNCHED_PROJECT]
        return dataclasses.replace(
            super().declare(), resource_kind=kind, may_touch=frozenset({kind})
        )


class Watched:
    """The local port, watched: `rotate` runs right after the consumer is created (the host's
    credential rotates while the run waits), every observation of the owned process records its
    identity (pid, start), and a restart or recreate is recorded."""

    def __init__(
        self,
        inner: LocalProcessPort | LaunchCredential,
        selector: str,
        rotate: Any = None,
    ) -> None:
        self._inner, self._selector, self._rotate = inner, selector, rotate
        self.seen: list[tuple[int, Any]] = []
        self.repairs: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create(self, spec: Any, ticket: Any) -> Any:
        answer = self._inner.create(spec, ticket)
        if self._rotate is not None:
            self._rotate()
        return answer

    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        answer = self._inner.observe(spec, lineage, effect)
        instance = self._inner._instances.get(self._selector)  # noqa: SLF001 (the rig's witness)
        if answer.selector_present and instance is not None:
            self.seen.append((instance.proc.pid, instance.start))
        return answer

    def restart(self, target: Any, ticket: Any) -> Any:
        self.repairs.append("restart")
        return self._inner.restart(target, ticket)

    def recreate(self, target: Any, ticket: Any) -> Any:
        self.repairs.append("recreate")
        return self._inner.recreate(target, ticket)


def port_map(watched: Watched, grant: Any, delivery: Any) -> dict[type, object]:
    return {
        ports.ResourceReads: watched,
        ports.ResourceCreate: watched,
        ports.ResourceOwned: watched,
        ports.GrantReads: grant,
        ports.HostScopeReads: DemoHostScope(grant, now=lambda: kit.NOW),
        ports.GrantDelivery: delivery,
    }


def stale_remedy_rows(
    rows: list[dict[str, Any]], unit: str = UNIT, effect: str = consumers.DELIVER
) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r.get("path") == unit and r["class"] == "issue" and r["effect"] == effect
        if r["remedy"]["code"] == codes.CREDENTIAL_STALE
    ]


# ---------------------------------------------------------------------------
# the owned process restarted on an older generation (L.RB-9.5.fix1)
# ---------------------------------------------------------------------------


class RestartingConsumerUnit(LocalConsumerUnit):
    """A local app that reads its credential at start: no `deliver` effect; `CREDENTIAL_STALE` is
    repaired by one `restart` of the run's own handle (OWNED, so a found process is never granted
    it, J-13)."""

    def __init__(self, command: BoundCommand) -> None:
        super().__init__(command, STALE_UNIT)

    def declare(self) -> LeafDeclaration:
        base = super().declare()
        release = timedelta(seconds=consumers.RELEASE_TIMEOUT_S)
        effects = tuple(
            EffectDeclaration(
                RESTART, EffectFacetClass.OWNED, "", Lifetime.RUN, frozenset(), release
            )
            if effect.effect == consumers.DELIVER
            else effect
            for effect in base.effects
        )
        remedy = RemedyDeclaration(
            codes.CREDENTIAL_STALE,
            RESTART,
            1,
            timedelta(seconds=consumers.WAIT_S),
            timedelta(0),
        )
        return dataclasses.replace(base, effects=effects, remedies=(remedy,))

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if state.remedy is not None and state.owned:
            effects.owned(ports.ResourceOwned).restart(state.owned[-1], state.remedy.effect)
            return Acted()
        return super().advance(params, state, effects, ctx)


class LaunchCredential:
    """The local port with the credential a process takes at start: every launch (`create`,
    `restart`) first calls `issue(selector)`, which puts the issuer's CURRENT credential where the
    app reads it, then is the port's own launch (it returns once the new process has reported its
    endpoint). Every other member is the port's own."""

    def __init__(self, inner: LocalProcessPort, issue: Callable[[str], None]) -> None:
        self._inner, self._issue = inner, issue

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create(self, spec: Any, ticket: Any) -> Any:
        self._issue(run_scoped_selector(ticket.lineage, ticket.effect))
        return self._inner.create(spec, ticket)

    def restart(self, target: Any, ticket: Any) -> Any:
        self._issue(target.selector)
        return self._inner.restart(target, ticket)


@dataclasses.dataclass
class StaleRestart:
    """What one run of the case left: the rig, the watched port, the app's log, its selector."""

    rig: Any
    watched: Watched
    log: Path
    selector: str


def stale_restart_case(
    tmp_path: Path,
    run_id: str,
    grant: Any,
    delivery: Any,
    issue: Callable[[str], None],
    rotate: Callable[[], Any],
) -> StaleRestart:
    """Run `RestartingConsumerUnit` once over the real `LocalProcessPort`: `grant` reads the
    app's generation (`GrantReads`), `issue` gives a launch the current credential, and `rotate`
    moves the issuer on right after the first launch."""
    selector = selector_for(run_id, STALE_UNIT)
    log = tmp_path / "app-events.log"
    command = app_command(log)
    launcher = LaunchCredential(LocalProcessPort(), issue)
    watched = Watched(launcher, selector, rotate=rotate)
    rig = tk.tree_rig(
        tmp_path,
        tk.group("consumers", (tk.bind(STALE_UNIT),)),
        {STALE_UNIT: RestartingConsumerUnit(command)},
        deadline_s=600,
        run_id=run_id,
        port_impl=port_map(watched, grant, delivery),
    )
    rig.run()
    return StaleRestart(rig, watched, log, selector)


def assert_restarted(case: StaleRestart) -> None:
    """The facts both the HOST node and its twin assert (the generation checks are the caller's:
    each reads its own issuer)."""
    rig, watched = case.rig, case.watched
    end = rig.ends()[STALE_UNIT]
    assert end["condition"] == "satisfied", end
    restarted = stale_remedy_rows(rig.rows(), STALE_UNIT, RESTART)
    assert len(restarted) == 1, rig.rows()  # one restart, the remedy's only attempt
    issues = [r for r in rig.rows() if r.get("path") == STALE_UNIT and r["class"] == "issue"]
    effects = [r["effect"] for r in issues]
    assert effects.count(consumers.UP) == 1 and effects.count(RESTART) == 1, issues  # no 2nd create
    assert consumers.DELIVER not in effects, issues  # nothing reaches the app but a launch
    assert watched.repairs == ["restart"]  # a restart of the run's own handle, never a recreate
    pids = [pid for pid, _start in watched.seen]
    assert len(set(pids)) == 2 and pids[-1] != pids[0], watched.seen  # a new process after it
    assert case.log.read_text().splitlines() == ["listening", "stop", "listening", "stop"]
    answer = tk.answer_of(rig)
    assert str(answer.outcome) == "passed", answer
    assert str(answer.primary.node_class) == "repaired", answer  # passed, with a repair


__all__ = [
    "CHANNEL_FILE",
    "LaunchCredential",
    "LocalConsumerUnit",
    "RestartingConsumerUnit",
    "StaleRestart",
    "Watched",
    "assert_restarted",
    "stale_restart_case",
]
