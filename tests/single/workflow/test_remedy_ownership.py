"""L.SL-6.2: the remedy ownership boundary (B1-E3; WR-REMEDY-4, WR-OWN-10, WR-REMEDY-5).

"A remedy whose effect is OWNED is authorized only against `state.owned`; a remedy that changes a
found resource must be SAFE_START." Three levels:

- registration: an OWNED remedy on a leaf that creates nothing (its resource is found, so
  `state.owned` is always empty) is refused at publication, `publication.owned_remedy_on_found`,
  naming the remedy; a SAFE_START remedy on the same leaf is registrable, and only with the verbs
  start, refresh or install (B1-E1, L.SL-7.1). Nothing is snapshotted, no run can start;
- the loop: over a found resource that is unhealthy the loop never grants the OWNED remedy and no
  owned effect is issued, so the found resource is never restarted (a found resource is never
  owned: V-4.2);
- an assertion failure: a `RECORDED` leaf's failing test result joins FAILED with no remedy
  granted, even for a code a remedy table names (WR-REMEDY-5), and a `RECORDED` leaf that declares
  remedies is not registrable (B1-E2)."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.single.contract import declared_fixtures as fx
from tests.single.workflow.joinkit import APPLIED, obs, record, terms, ticket
from tests.single.workflow.test_join_table import run, shape
from tests.single.workflow.test_remediation import _remedy_tickets, _rig
from trestle.common.plan import vocabulary as vocab
from trestle.common.types import PublishView, RequestOutcome
from trestle.server.main import create_kernel
from trestle.workflow import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    Lifetime,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WorkflowEntry,
    codes,
)
from trestle.workflow.extract import ExtractionRefused, extract_declared_tree
from trestle.workflow.registration import SAFE_START_VERBS, check_declaration
from trestle.workflow.values import Condition as C
from trestle.workflow.values import Provenance as P
from trestle.workflow.values import RecordedResult

REMEDY_CODE = "marker.hot"
SAFE_EFFECT = "kick"
OWNED_EFFECT = "restart"
CODE = vocab.OWNED_REMEDY_ON_FOUND

SOURCE = """
from __future__ import annotations

from datetime import timedelta

from trestle.plugin import Context, trestle
from trestle.workflow import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)

CREATE = EffectDeclaration("up", EffectFacetClass.CREATE, "", Lifetime.RUN, frozenset(),
                           timedelta(seconds=2))
OWNED = EffectDeclaration("restart", EffectFacetClass.OWNED, "", Lifetime.RUN, frozenset(), None)
KICK = EffectDeclaration("kick", EffectFacetClass.SAFE_START, "refresh", Lifetime.DURABLE,
                         frozenset(), None)
REMEDY = RemedyDeclaration("marker.hot", "EFFECT_ID", 2, timedelta(seconds=6),
                           timedelta(seconds=1))


class Unit:
    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit="unit",
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=10)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(EFFECTS),
            retryable=frozenset(),
            remedies=(REMEDY,),
            budget=timedelta(seconds=60),
            max_attempts=3,
        )


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=60))


@trestle
def wf(ctx: Context, name: str = "x") -> dict[str, str]:
    return {"name": name}
"""


def _source(effects: str, remedy_effect: str, name: str = "wf") -> str:
    text = SOURCE.replace("EFFECTS", effects).replace("EFFECT_ID", remedy_effect)
    return text.replace("def wf(", f"def {name}(")


def _effect(**changes: Any) -> EffectDeclaration:
    base = EffectDeclaration(
        effect=OWNED_EFFECT,
        facet=EffectFacetClass.OWNED,
        verb="",
        lifetime=Lifetime.RUN,
        host_sections=frozenset(),
        release_timeout=None,
    )
    return dataclasses.replace(base, **changes)


def _remedy(effect: str = OWNED_EFFECT) -> RemedyDeclaration:
    return RemedyDeclaration(REMEDY_CODE, effect, 2, timedelta(seconds=6), timedelta(seconds=1))


def _leaf(effects: tuple[EffectDeclaration, ...], *remedies: RemedyDeclaration) -> Any:
    return dataclasses.replace(fx.leaf_declaration(), effects=effects, remedies=tuple(remedies))


def _found_leaf(effect: EffectDeclaration, remedy_effect: str) -> Any:
    """A leaf that creates nothing: its resource is found, so it owns nothing."""
    return _leaf((effect,), _remedy(remedy_effect))


def _only(decl: Any) -> Any:
    refusals = check_declaration(decl)
    assert len(refusals) == 1, refusals
    return refusals[0]


# ------------------------------------------------------------------------------ registration


def test_owned_remedy_refused_on_found_resource() -> None:
    """B1-E3: an OWNED remedy needs something owned to target. A leaf with no CREATE effect owns
    nothing, so the refusal is `publication.owned_remedy_on_found`, naming the remedy's code."""
    refusal = _only(_found_leaf(_effect(), OWNED_EFFECT))
    assert refusal.code == CODE == "publication.owned_remedy_on_found"
    assert refusal.element == f"remedies[{REMEDY_CODE}].effect"
    assert refusal.message.startswith(f"{refusal.element}:") and OWNED_EFFECT in refusal.message
    with pytest.raises(ExtractionRefused) as extraction:
        extract_declared_tree(
            WorkflowEntry(
                root="svc",
                units={"svc": _found_leaf(_effect(), OWNED_EFFECT)},
                deadline=timedelta(seconds=60),
            )
        )
    assert extraction.value.code == CODE and REMEDY_CODE in extraction.value.message

    # the same OWNED remedy is fine once the leaf creates what it repairs
    create = dataclasses.replace(
        fx.leaf_declaration().effects[0], effect="up", release_timeout=timedelta(seconds=2)
    )
    assert check_declaration(_leaf((create, _effect()), _remedy())) == ()
    # a release effect is owned too, and a leaf that creates nothing owns nothing to release
    stop = _effect(effect="stop", is_release=True)
    assert _only(_found_leaf(stop, "stop")).code == CODE

    # one refusal per remedy that reaches for an owned effect; other remedies are not its concern
    both = _leaf(
        (_effect(), _effect(effect="recreate")),
        _remedy(),
        dataclasses.replace(_remedy("recreate"), code="marker.gone"),
    )
    assert [r.element for r in check_declaration(both)] == [
        f"remedies[{REMEDY_CODE}].effect",
        "remedies[marker.gone].effect",
    ]


def test_safe_start_verbs_only() -> None:
    """A found resource is changed only by a SAFE_START effect whose verb is start, refresh or
    install: a SAFE_START remedy on a leaf that creates nothing registers, and any other verb is
    `publication.safe_start_verb_invalid` (never a second, ownership, refusal)."""
    assert SAFE_START_VERBS == {"start", "refresh", "install"}
    for verb in sorted(SAFE_START_VERBS):
        kick = _effect(
            effect=SAFE_EFFECT,
            facet=EffectFacetClass.SAFE_START,
            verb=verb,
            lifetime=Lifetime.DURABLE,
        )
        assert check_declaration(_found_leaf(kick, SAFE_EFFECT)) == (), verb
    for verb in ("restart", "recreate", "stop", "delete", ""):
        kick = _effect(
            effect=SAFE_EFFECT,
            facet=EffectFacetClass.SAFE_START,
            verb=verb,
            lifetime=Lifetime.DURABLE,
        )
        refusal = _only(_found_leaf(kick, SAFE_EFFECT))
        assert refusal.code == vocab.SAFE_START_VERB_INVALID, verb
        assert refusal.element == f"effects[{SAFE_EFFECT}].verb"


@pytest.mark.proves("A7.3", "A7.3", "A", "single", "LOGIC+MCP", "CI")
def test_registration_refuses_owned_remedy_on_found(tmp_path: Path) -> None:
    """Through the MCP `publish_plugin` call: the stable code and the remedy it names, nothing
    promoted, the previous snapshot keeps serving, a refused first publish registers nothing; the
    SAFE_START remedy on the same leaf publishes."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = create_kernel(home=tmp_path / "trestle", plugin_dirs=[plugin_dir], skip_recovery=True)
    good = kernel.control.publish_plugin(_source("KICK,", "kick"))
    assert isinstance(good, PublishView), good  # a SAFE_START remedy on a found resource
    snapshots = tmp_path / "trestle" / "snapshots"
    before = sorted(p.name for p in snapshots.iterdir())

    refused = kernel.control.publish_plugin(_source("OWNED,", OWNED_EFFECT))
    assert isinstance(refused, RequestOutcome)
    assert refused.code == CODE and refused.origin == "publication" and not refused.retryable
    assert REMEDY_CODE in refused.message and OWNED_EFFECT in refused.message
    assert sorted(p.name for p in snapshots.iterdir()) == before  # nothing promoted
    desc = kernel.control.describe_plugin("wf")
    assert isinstance(desc, dict) and desc["snapshot_id"] == good.snapshot_id

    first = kernel.control.publish_plugin(_source("OWNED,", OWNED_EFFECT, name="other"))
    assert isinstance(first, RequestOutcome) and first.code == CODE
    assert kernel.registry.get("other") is None
    assert sorted(p.name for p in snapshots.iterdir()) == before

    # with a CREATE effect beside it the very same OWNED remedy publishes
    creating = kernel.control.publish_plugin(
        _source("CREATE, OWNED,", OWNED_EFFECT, name="creating")
    )
    assert isinstance(creating, PublishView), creating


# ------------------------------------------------------------------------------ the loop


def test_found_resource_is_never_restarted(tmp_path: Path) -> None:
    """A found marker that reports the remedy's trigger code and never heals: the loop grants no
    OWNED remedy (`Verdict.owned` is empty, V-4.2), issues no owned ticket for it and answers
    FOUND_UNHEALTHY; the found marker is left exactly as it was."""
    rig, unit, marker = _rig(tmp_path, attempts=2, fixed=True)
    found = marker.plant_found("marker")
    before = marker._read(found)
    rig.run()
    assert marker.restarts == 0 and _remedy_tickets(rig) == []
    assert "remedy" not in unit.log and "advance" not in unit.log, unit.log
    issued = [t["effect"] for t in rig.rows("issue")]
    assert issued == [], f"nothing is issued against a found resource: {issued}"
    (end,) = rig.ends()
    assert end["provenance"] == "found" and end["condition"] != "satisfied", end
    assert end["code"] == codes.FOUND_UNHEALTHY, end
    assert marker._record(found) is not None and marker._read(found) == before


# ------------------------------------------------------------------------------ assertions


def test_assertion_failure_triggers_no_remediation() -> None:
    """WR-REMEDY-5: a `RECORDED` (test) leaf's failing result joins FAILED with that result's own
    code and no remedy grant, even when a remedy table names the code (which registration would
    already have refused, B1-E2); the observed twin of the same failure does get its remedy."""
    failing = RecordedResult(False, REMEDY_CODE, None)
    remedies = (RemedyDeclaration(REMEDY_CODE, "fix", 2, timedelta(300), timedelta(1)),)
    recorded = terms(completion=CompletionSource.RECORDED, repeat=Repeat.ONCE, remedies=remedies)
    ran = record(tickets=(ticket(status=APPLIED, result=failing, repeat=Repeat.ONCE),))
    verdict = run(recorded, obs(selector_present=True, post=False, code=REMEDY_CODE), ran, now=10)
    assert shape(verdict) == (P.CREATED, C.FAILED, REMEDY_CODE) and verdict.remedy is None

    # the observed leaf with the same code gets the grant: the difference is the completion source
    observed = terms(remedies=remedies)
    created = record(tickets=(ticket(status=APPLIED, with_handle=True),))
    granted = run(observed, obs(selector_present=True, post=False, code=REMEDY_CODE), created, 10)
    assert granted.remedy is not None and granted.remedy.code == REMEDY_CODE

    # and a recorded leaf that declares remedies is not registrable
    flags = LoopFlags(Compose.LEAF, CompletionSource.RECORDED, Repeat.ONCE)
    leaf = dataclasses.replace(fx.leaf_declaration(), flags=flags)
    assert [r.code for r in check_declaration(leaf)] == [vocab.RECORDED_WITH_REMEDIES]
