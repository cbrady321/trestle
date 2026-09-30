"""A child that creates a process and a resource, beside a sibling that ends the run (L.TR-4.3;
MC-B3-01 behaviour fixture; consumed by L.TR-L.7).

`app` runs two leaves at once. `c` creates a process `P` (effect `spawn_p`) and a resource `K`
(effect `make_k`), both fake in-run markers of `FakeMarker`: `P` is a real child process of the
run (the process table shows it), `K` is a marker whose observable is the fake's own inventory.
`sib` waits until both exist and then ends the run the way the request's `mode` says:

- `sibling_fail`: it fails (an ordinary failure: `c` is not stopped and runs to its own end);
- `exception`: it raises from `advance` (a whole-root stop: `c` is cut mid-wait);
- `hold`: it holds, and so does `c`, until something outside stops the run (a root cancel);
- `deadline`: as `hold`, but `c` blocks inside one `observe` call for far longer than the root's
  deadline, so it is the release point of the root deadline that ends it (a polling child would
  end itself at its own carved slice, which is earlier).

`sib` shares `c`'s logical system, so a port that lists every other live instance as a found one
lists `P` and `K` to it: the runtime must not report them found (WR-UNIT-5). Three vertices,
depth 2."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import FakeMarker

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
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
    CreatedHandle,
    FoundRef,
    Observation,
    Verdict,
)

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references; `depth` is
# the longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 3, "depth": 2, "shared": None, "expect": "valid"}

CREATE_P = "spawn_p"
CREATE_K = "make_k"
STOP_EFFECT = "stop"
WAIT_MAX_S = 6
RELEASE_TIMEOUT_S = 2
LEAF_BUDGET_S = 10
ROOT_BUDGET_S = 20
DEADLINE_S = 36
HOLD_S = 120  # `deadline` mode: one blocked call, far past the root's deadline
LOOK_S = 0.1  # how often `sib` looks for `c`'s resources
LOOKS = 100
LINGER_S = 0.5  # `sib` lets `c`'s resources be seen this long before it ends the run

# The run's mode and its marker, set by the plugin function before `run_tree` (a unit is built at
# import, the request is known only at run time).
RUN: dict[str, Any] = {"mode": "hold", "marker": None}

SPEC = ResourceSpec("creator_pk", RealizationKind.AGENT_LAUNCHED_PROJECT, "creator-pk", None)


def _declaration(
    unit: str, *, effects: tuple[EffectDeclaration, ...], max_attempts: int = 1
) -> LeafDeclaration:
    return LeafDeclaration(
        unit=unit,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=WAIT_MAX_S)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=effects,
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=LEAF_BUDGET_S),
        max_attempts=max_attempts,
    )


def _create(effect: str) -> EffectDeclaration:
    return EffectDeclaration(
        effect,
        EffectFacetClass.CREATE,
        "",
        Lifetime.RUN,
        frozenset(),
        timedelta(seconds=RELEASE_TIMEOUT_S),
    )


def _observation(
    seen: Any, ready: bool, found: tuple[Any, ...], present: bool | None = None
) -> Observation:
    return Observation(
        present=(seen.selector_present or bool(found)) if present is None else present,
        selector_present=seen.selector_present,
        identity_proven=seen.identity_proven,
        configuration_compatible=seen.configuration_compatible,
        postcondition=CheckResult(ready, None, ""),
        preconditions=(),
        currency=(),
        found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in found),
        code=seen.code,
        payload=None,
    )


class Creator:
    """Creates `P`, then `K`; in `sibling_fail` mode it is done once both are healthy, in every
    other mode it never is (so it is still converging when the run is stopped). The absence poll
    of a release (V-3.8) reads one handle's resource, so `release` names the effect it is giving
    back and `observe` reports that resource alone until the next release call."""

    def __init__(self) -> None:
        self._releasing: str | None = None

    def declare(self) -> LeafDeclaration:
        return _declaration(
            "c",
            max_attempts=2,  # one stop ticket per handle (P, K): attempts count per effect
            effects=(
                _create(CREATE_P),
                _create(CREATE_K),
                EffectDeclaration(
                    STOP_EFFECT,
                    EffectFacetClass.OWNED,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=RELEASE_TIMEOUT_S),
                    is_release=True,
                ),
            ),
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        if self._releasing is not None:
            gone = resource.observe(SPEC, ctx.lineage, self._releasing)
            return _observation(gone, False, ())
        made_p = resource.observe(SPEC, ctx.lineage, CREATE_P)
        made_k = resource.observe(SPEC, ctx.lineage, CREATE_K)
        both = made_p.selector_present and made_k.selector_present
        if both and RUN["mode"] == "deadline":
            ctx.cancellation.wait(timedelta(seconds=HOLD_S))  # blocked inside one call
        healthy = False
        if both and RUN["mode"] == "sibling_fail":
            healthy = (
                resource.check("ready", made_p.selector_ref).satisfied
                and resource.check("ready", made_k.selector_ref).satisfied
            )
        return _observation(made_k, healthy, made_k.found)

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        effects.create(ResourceCreate).create(SPEC, CREATE_P)
        effects.create(ResourceCreate).create(SPEC, CREATE_K)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        self._releasing = handle.effect
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


class Sibling:
    """Waits until `c` has made both resources, then acts by the run's mode."""

    def declare(self) -> LeafDeclaration:
        return _declaration("sib", effects=())

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        marker = RUN["marker"]
        for _ in range(LOOKS):
            if len(marker.inventory()["containers"]) >= 2:
                break
            if ctx.cancellation.wait(timedelta(seconds=LOOK_S)):
                break
        seen = reads.read(ResourceReads).observe(SPEC, ctx.lineage, None)
        return _observation(seen, False, seen.found)

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        mode = RUN["mode"]
        if mode in ("exception", "sibling_fail"):
            ctx.cancellation.wait(timedelta(seconds=LINGER_S))
        if mode == "exception":
            raise RuntimeError("fixture: the sibling raised")
        if mode == "sibling_fail":
            return Failed("fixture.sibling_failed", "the sibling failed after c created P and K")
        ctx.cancellation.wait(timedelta(seconds=HOLD_S))  # held until a stop wakes it
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        raise NotImplementedError("the sibling creates nothing")


ENTRY = WorkflowEntry(
    root="app",
    units={
        "app": AllDeclaration(
            unit="app",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="c", params={}, needs=()),
                ChildBinding(unit="sib", params={}, needs=()),
            ),
            concurrency=2,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "c": Creator(),
        "sib": Sibling(),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=36, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def creator_pk(ctx: Context, env: str = "dev", mode: str = "hold") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    RUN["mode"] = mode
    RUN["marker"] = marker
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env, "mode": mode}
