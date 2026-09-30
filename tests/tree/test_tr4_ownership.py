"""L.TR-4.1: a child's claim enters the root's ownership record before the effect; a resource one
node created is "created by this run" for every other node under the root and never "found"
(WR-UNIT-5, WR-UNIT-1, WR-OWN-1).

The tree is run in-library through the loop (MC-26's rig: the real child services and attempt lane
under a manual clock). Every claim is read from the run's one lane (MC-19), in record order. The
marker port keys a resource by `(node path, effect)` and reports every other live resource as a
`FoundRef`, as an adapter that cannot tell one root's resources apart from a stranger's does
(`FakeMarker` for one logical system): the case the rule is about."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.proof import records
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
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

proves_claim = pytest.mark.proves(
    "WR-UNIT-5", "WR-UNIT-5:claim-before-effect", "A", "tree", "PROC", "CI"
)
proves_sibling = pytest.mark.proves(
    "WR-UNIT-5", "WR-UNIT-5:sibling-not-found", "A", "tree", "PROC", "CI"
)
proves_created = pytest.mark.proves(
    "WR-UNIT-1", "WR-UNIT-1:created-not-found", "A", "tree", "LOGIC+PROC+MCP", "CI"
)
proves_root_record = pytest.mark.proves("WR-OWN-1", "WR-OWN-1:tree", "A", "tree", "PROC", "CI")

SIDE = "side"  # a second creating effect of one node (test_created_then_healthy_not_found)
STOP_TIMEOUT = timedelta(seconds=2)
EXECUTABLE = "/opt/engine/ctl"


class SharedMarker(tk.PathMarker):
    """An in-memory marker port for a tree, keyed by `(node path, effect)`. `observe` addresses
    the instance of this node and effect (`selector_present`); every other live instance, and each
    planted one, is a `FoundRef`. `create` calls `on_create(path, effect)` after its ticket was
    issued, before it changes anything. The release descriptor is an `ArgvRelease` that names the
    creating node's own selector, so each claim carries a descriptor of its own (V-10.1)."""

    def __init__(self, on_create: Any = None) -> None:
        super().__init__()
        self.on_create_effect = on_create
        self.planted: set[str] = set()

    @staticmethod
    def selector(lineage: Lineage, effect: str) -> str:
        return f"sel-{'/'.join(lineage.path.segments)}/{effect}"

    def plant(self, selector: str) -> None:
        """A resource this run did not make (present before it started): only ever `found`."""
        self.planted.add(selector)

    def observe(
        self, spec: ports.ResourceSpec, lineage: Lineage, effect: str | None
    ) -> ports.ResourceObservation:
        mine = self.selector(lineage, effect or kit.EFFECT)
        with self._lock:
            self.calls.append(("observe", "/".join(lineage.path.segments)))
            present = mine in self._live
            others = sorted((self._live | self.planted) - {mine})
        ref = SelectorRef(lineage, effect or kit.EFFECT, mine, kit.NOW) if present else None
        found = tuple(FoundRef(spec.logical_system, other, kit.NOW) for other in others)
        return ports.ResourceObservation(present, ref, True, True, (), found, None)

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        selector = self.selector(call.lineage, call.effect)
        return ports.ArgvRelease(
            executable=EXECUTABLE,
            observe_argv=("observe", selector),
            observe_ok_exit=frozenset({0}),
            stop_argv=("stop", selector),
            timeout=STOP_TIMEOUT,
        )

    def create(self, spec: ports.ResourceSpec, ticket: Any) -> Confirmation:
        path = "/".join(ticket.lineage.path.segments)
        selector = self.selector(ticket.lineage, ticket.effect)
        with self._lock:
            self.calls.append(("create", path))
        if self.on_create_effect is not None:
            self.on_create_effect(path, ticket.effect)
        with self._lock:
            self._live.add(selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, selector)

    def stop(self, target: CreatedHandle, ticket: Any) -> Confirmation:
        with self._lock:
            self.calls.append(("stop", "/".join(ticket.lineage.path.segments)))
            self._live.discard(target.selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, None)


def _observing_unit(
    name: str, effects: tuple[str, ...] = (kit.EFFECT,), *, reuse_found: bool = False
) -> kit.Unit:
    """A leaf that creates one resource per effect of `effects` and observes the first. The
    observation carries the port's `found` as it is. `reuse_found`: a leaf that takes any found
    instance for its own and is content with it (the reuse the rule forbids for a resource this
    run created, and the rule's control for a stranger's)."""
    declared = tuple(kit.effect(e, kit.EffectFacetClass.CREATE) for e in effects)
    decl = replace(
        kit.declaration(
            effects=(
                *declared,
                kit.effect(kit.STOP_EFFECT, kit.EffectFacetClass.OWNED, release=True),
            ),
            max_attempts=1,
            budget_s=tk.LEAF_BUDGET_S,
            max_wait_s=5.0,
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
            postcondition=CheckResult(ready, None, ""),
            preconditions=(),
            currency=(),
            found=seen.found,
            code=None,
            payload=None,
        )

    def advance(
        unit: kit.Unit, params: Any, state: Verdict, facets: EffectFacets, ctx: ActContext
    ) -> Step:
        for effect in effects:
            facets.create(ports.ResourceCreate).create(kit.SPEC, effect)
        return Acted()

    def release(
        unit: kit.Unit, params: Any, handle: CreatedHandle, facets: Any, ctx: ActContext
    ) -> Step:
        facets.owned(ports.ResourceOwned).stop(handle, kit.STOP_EFFECT)
        return Acted()

    return kit.Unit(decl, observe, advance, release)


def _observed(rig: tk.TreeRig, path: str) -> list[dict[str, Any]]:
    return [
        fields
        for kind, fields in rig.rig.sink.events
        if kind == "step.observed" and fields["path"] == path
    ]


def _lane(rig: tk.TreeRig) -> list[dict[str, Any]]:
    return [row.entry for row in records.lane_rows(rig.run_dir).rows]


@proves_claim
@proves_root_record
def test_child_claim_before_effect(tmp_path: Path) -> None:
    """Three creators at two levels (`mid/a`, `mid/b`, `c`). At the moment each one's create runs,
    the root's lane already holds its issue entry (the claim: the child's path, the run-scoped
    descriptor) and no confirmation of it; afterwards every confirmation follows its claim."""
    at_create: dict[str, list[dict[str, Any]]] = {}

    def snapshot(path: str, effect: str) -> None:
        at_create[path] = _lane(rig)

    mid = tk.group("mid", (tk.bind("a"), tk.bind("b", "a")), concurrency=2, budget_s=100)
    app = tk.group("app", (tk.bind("mid"), tk.bind("c", "mid")), concurrency=2, budget_s=250)
    units: dict[str, object] = {"mid": mid, **{n: _observing_unit(n) for n in ("a", "b", "c")}}
    rig = tk.tree_rig(tmp_path, app, units, SharedMarker(snapshot))
    rig.run()

    creators = ["mid/a", "mid/b", "c"]
    assert sorted(at_create) == sorted(creators)
    for path in creators:
        rows = at_create[path]
        claims = [r for r in rows if r["class"] == "issue" and r["path"] == path]
        assert len(claims) == 1, f"{path}: the claim is durable before the effect"
        claim = claims[0]
        assert claim["effect"] == kit.EFFECT and claim["attempt"] == 1
        # the descriptor is the port's, derived from the run-scoped selector before the effect
        assert claim["release"]["form"] == "argv"
        assert claim["release"]["stop_argv"] == ["stop", f"sel-{path}/{kit.EFFECT}"]
        assert not [r for r in rows if r["class"] == "confirmation" and r["path"] == path]
        assert rows[0]["class"] == "plan"  # the plan entry precedes every claim (V-4.8)
    # one lane serves the whole tree, and every confirmation follows its own claim
    final = _lane(rig)
    for path in creators:
        seqs = {
            r["class"]: r["seq"]
            for r in final
            if r.get("path") == path and r.get("effect") == kit.EFFECT and r["class"] != "released"
        }
        assert seqs["issue"] < seqs["confirmation"]
    assert {r["path"] for r in final if r["class"] == "issue"} >= set(creators)


@proves_sibling
def test_sibling_sees_created_not_found(tmp_path: Path) -> None:
    """`c` creates a resource; `s` (which needs `c`) observes it. The port reports it to `s` as a
    found instance, as it would any instance its selector does not address. The run created it, so
    `s` never reads it as found: its observations list nothing found, name it as created by the
    run, and `s` does not reuse it (it makes its own, and joins `CREATED`, never `FOUND`)."""
    marker = SharedMarker()
    app = tk.group("app", (tk.bind("c"), tk.bind("s", "c")), concurrency=2)
    units: dict[str, object] = {
        "c": _observing_unit("c"),
        "s": _observing_unit("s", reuse_found=True),
    }
    rig = tk.tree_rig(tmp_path, app, units, marker)
    rig.run()

    creator = f"sel-c/{kit.EFFECT}"
    seen = _observed(rig, "s")
    assert seen, "the sibling observed"
    for fields in seen:
        assert fields["found"] == [], "a resource the run created is never found"
        assert fields["present"] is fields["selector_present"]  # `present` follows the filter
    assert seen[0]["created_by_run"] == [creator]  # its first look, before it made anything
    ends = rig.ends()
    assert (ends["s"]["condition"], ends["s"]["provenance"]) == ("satisfied", "created")
    assert ends["c"]["provenance"] == "created"
    # it made its own resource rather than reusing the creator's
    assert marker.paths("create") == ["c", "s"]
    handles = {
        r["path"]: r["identity"]
        for r in _lane(rig)
        if r["class"] == "confirmation" and r["identity"]
    }
    assert handles == {"c": creator, "s": f"sel-s/{kit.EFFECT}"}


@proves_sibling
def test_a_strangers_resource_is_still_found(tmp_path: Path) -> None:
    """The rule's control: an instance the record does not hold (present before the run) is still
    `found` by a node that observes it, and a node content with it reuses it and creates nothing."""
    marker = SharedMarker()
    marker.plant("stranger")
    app = tk.group("app", (tk.bind("s"),), concurrency=1)
    rig = tk.tree_rig(tmp_path, app, {"s": _observing_unit("s", reuse_found=True)}, marker)
    rig.run()

    seen = _observed(rig, "s")
    assert [f["selector"] for f in seen[0]["found"]] == ["stranger"]
    assert "created_by_run" not in seen[0]
    ends = rig.ends()
    assert (ends["s"]["condition"], ends["s"]["provenance"]) == ("satisfied", "found")
    assert marker.paths("create") == []  # reused, never touched
    assert not [r for r in _lane(rig) if r["class"] == "issue"]


@proves_created
def test_created_then_healthy_not_found(tmp_path: Path) -> None:
    """Called directly, the unit's record is the root record: one node makes two resources (`up`
    and `side`, one system) and observes `up`, healthy. The port lists `side` as a found instance;
    the run made it, so it is never reported found by any later observation."""
    marker = SharedMarker()
    rig = tk.tree_rig(tmp_path, _observing_unit("solo", (kit.EFFECT, SIDE)), {}, marker)
    rig.run()

    seen = _observed(rig, "(root)")
    assert len(seen) >= 2  # before the creation, and after it
    assert seen[0]["found"] == [] and "created_by_run" not in seen[0]  # nothing existed yet
    healthy = seen[-1]
    assert healthy["selector_present"] is True
    assert healthy["found"] == []
    assert healthy["created_by_run"] == [f"sel-/{SIDE}"]
    ends = rig.ends()
    assert (ends[""]["condition"], ends[""]["provenance"]) == ("satisfied", "created")
