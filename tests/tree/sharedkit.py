"""A marker port and a creating leaf for the TR-4 ownership and release proofs (L.TR-4.1, 4.2).

`SharedMarker` is the in-memory port behind `ResourceReads`, `ResourceCreate` and `ResourceOwned`
for a whole tree, keyed by `(node path, effect)` (`treekit.PathMarker` keys by node alone). It is
a stand-in for an adapter that cannot tell one root's resources from a stranger's: `observe`
answers `selector_present` for the instance the caller's own selector addresses and lists every
other live instance as a `FoundRef`, exactly as `FakeMarker` does for one logical system. Its
release descriptor is an `ArgvRelease` in the shape the host sweep's stub engine
(`tests/single/control/sweep_stub.py`) speaks, addressing the creating node's own run-scoped
selector, so a run's record can be swept with no plugin code (V-10.1).

`observing_unit` is a leaf that creates one resource per declared effect, observes the first and
passes the port's `found` through unchanged."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from tests.single.control import sweep_stub as sw
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.declarations import RemedyDeclaration
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
    Failed,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import (
    CheckResult,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    Lineage,
    Observation,
    SelectorRef,
    Verdict,
)

HOT = "marker.hot"  # the code an unhealthy marker reports until it is recreated
REMEDY = RemedyDeclaration(HOT, "recreate", 2, timedelta(seconds=6), timedelta(seconds=1))


def text_of(lineage: Lineage) -> str:
    return "/".join(lineage.path.segments)


class SharedMarker(tk.PathMarker):
    """The port. `on_create(path, effect)` runs inside `create`, after its ticket was issued and
    before anything changes. `plant(selector)` adds an instance this run did not make. `hot` holds
    the selectors that report `HOT` (never ready) until they are recreated; `recreate` keeps the
    resource live, clears the flag and counts the call."""

    def __init__(self, on_create: Callable[[str, str], None] | None = None) -> None:
        super().__init__()
        self.on_create_effect = on_create
        self.planted: set[str] = set()
        self.hot: set[str] = set()
        self.recreated: list[str] = []
        self.hook: dict[str, Callable[[], None]] = {}  # verb -> called inside that verb's effect

    @staticmethod
    def selector(lineage: Lineage, effect: str) -> str:
        return f"sel-{text_of(lineage)}/{effect}"

    def plant(self, selector: str) -> None:
        self.planted.add(selector)

    def live(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._live)

    def observe(
        self, spec: ports.ResourceSpec, lineage: Lineage, effect: str | None
    ) -> ports.ResourceObservation:
        mine = self.selector(lineage, effect or kit.EFFECT)
        with self._lock:
            self.calls.append(("observe", text_of(lineage)))
            present = mine in self._live
            others = sorted((self._live | self.planted) - {mine})
        ref = SelectorRef(lineage, effect or kit.EFFECT, mine, kit.NOW) if present else None
        found = tuple(FoundRef(spec.logical_system, other, kit.NOW) for other in others)
        return ports.ResourceObservation(present, ref, True, True, (), found, None)

    def check(self, check: str, target: Any) -> CheckResult:
        if target.selector in self.hot:
            return CheckResult(False, HOT, "hot")
        return CheckResult(True, None, "")

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        if call.member != "create":  # an owned effect: the handle's own descriptor (V-5.4 item 2)
            return ports.as_descriptor(call.arguments["target"].release)
        selector = self.selector(call.lineage, call.effect)
        return ports.ArgvRelease(
            executable=sw.EXE,
            observe_argv=("obs", selector),
            observe_ok_exit=frozenset({0}),
            stop_argv=("stop", selector),
            timeout=sw.STEP,
            remove_argv=("rm", selector),
        )

    def create(self, spec: ports.ResourceSpec, ticket: Any) -> Confirmation:
        path = text_of(ticket.lineage)
        selector = self.selector(ticket.lineage, ticket.effect)
        with self._lock:
            self.calls.append(("create", path))
        if self.on_create_effect is not None:
            self.on_create_effect(path, ticket.effect)
        with self._lock:
            self._live.add(selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, selector)

    def recreate(self, target: Any, ticket: Any) -> Confirmation:
        with self._lock:
            self.calls.append(("recreate", text_of(ticket.lineage)))
            self.recreated.append(target.selector)
            self.hot.discard(target.selector)
            self._live.add(target.selector)
        if "recreate" in self.hook:
            self.hook["recreate"]()
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    def stop(self, target: CreatedHandle, ticket: Any) -> Confirmation:
        with self._lock:
            self.calls.append(("stop", text_of(ticket.lineage)))
            self._live.discard(target.selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, None)


def observing_unit(
    name: str,
    effects: tuple[str, ...] = (kit.EFFECT,),
    *,
    reuse_found: bool = False,
    releases: bool = True,
    fails_after_create: bool = False,
    remedy: bool = False,
) -> kit.Unit:
    """A leaf that creates one resource per effect of `effects` and observes the first. Its
    observation carries the port's `found` as it is. `reuse_found`: a leaf that takes any found
    instance for its own and is content with it. `releases=False`: a leaf whose plugin is dead
    when the release pass would run (its `release` gives nothing back). `fails_after_create`: the
    leaf fails once its resources exist. `remedy`: the leaf declares `HOT -> recreate` and calls
    the owned `recreate` when the loop grants it."""
    declared = tuple(kit.effect(e, kit.EffectFacetClass.CREATE) for e in effects)
    owned = (kit.effect("recreate", kit.EffectFacetClass.OWNED, release_timeout_s=None),)
    decl = replace(
        kit.declaration(
            effects=(
                *declared,
                *(owned if remedy else ()),
                kit.effect(kit.STOP_EFFECT, kit.EffectFacetClass.OWNED, release=True),
            ),
            max_attempts=1,
            budget_s=tk.LEAF_BUDGET_S,
            max_wait_s=5.0,
            remedies=(REMEDY,) if remedy else (),
        ),
        unit=name,
    )

    def observe(unit: kit.Unit, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ports.ResourceReads)
        seen = resource.observe(kit.SPEC, ctx.lineage, effects[0])
        healthy = (
            resource.check("ready", seen.selector_ref) if seen.selector_ref is not None else None
        )
        ready = (healthy is not None and healthy.satisfied) or (reuse_found and bool(seen.found))
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=True,
            configuration_compatible=True,
            postcondition=CheckResult(ready, None if healthy is None else healthy.code, ""),
            preconditions=(),
            currency=(),
            found=seen.found,
            code=None if healthy is None else healthy.code,
            payload=None,
        )

    def advance(
        unit: kit.Unit, params: Any, state: Verdict, facets: EffectFacets, ctx: ActContext
    ) -> Step:
        if state.remedy is not None and state.owned:
            facets.owned(ports.ResourceOwned).recreate(state.owned[-1], state.remedy.effect)
            return Acted()
        for effect in effects:
            facets.create(ports.ResourceCreate).create(kit.SPEC, effect)
        if fails_after_create:
            return Failed("fixture.failed_after_create", "made its resources, then failed")
        return Acted()

    def release(
        unit: kit.Unit, params: Any, handle: CreatedHandle, facets: Any, ctx: ActContext
    ) -> Step:
        facets.owned(ports.ResourceOwned).stop(handle, kit.STOP_EFFECT)
        return Acted()

    return kit.Unit(decl, observe, advance, release if releases else None)
