"""L.SL-7.1: the single-vertex registration refusal set (B1-E1, B1-E2) at publication.

`check_declaration` is the one checker; extraction calls it, so a refused declaration promotes no
snapshot and no run can start for it. Each refusal carries its stable code (the single-level set in
`trestle.common.plan.vocabulary`) and names the declared element."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.single.contract import declared_fixtures as fx
from trestle.common.plan import vocabulary as vocab
from trestle.common.types import PublishView, RequestOutcome
from trestle.server.main import create_kernel
from trestle.server.plugin_validate import PublicationRefused
from trestle.server.snapshots import materialize_snapshot
from trestle.workflow import (
    AllDeclaration,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    Lifetime,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WorkflowEntry,
)
from trestle.workflow.extract import ExtractionRefused, extract_declared_tree
from trestle.workflow.registration import ELEMENTS, check_declaration

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

FIELDS = dict(
    unit="unit",
    flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
    preconditions=(),
    postcondition="ready",
    wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=30)),
    resource_kind="marker",
    may_touch=frozenset({"marker"}),
    effects=(),
    retryable=frozenset(),
    remedies=(),
    budget=timedelta(seconds=60),
    max_attempts=1,
)
FIELDS.update(OVERRIDES)


class Unit:
    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(**FIELDS)


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=60))


@trestle
def wf(ctx: Context, name: str = "x") -> dict[str, str]:
    return {"name": name}
"""

GOOD = "dict()"


def _source(overrides: str) -> str:
    return SOURCE.replace("OVERRIDES", overrides)


def _leaf(**changes: Any) -> Any:
    return dataclasses.replace(fx.leaf_declaration(), **changes)


def _flags(repeat: Any = Repeat.SAFE, completion: Any = CompletionSource.OBSERVED) -> LoopFlags:
    return LoopFlags(Compose.LEAF, completion, repeat)


def _effect(**changes: Any) -> EffectDeclaration:
    base = EffectDeclaration(
        effect="e",
        facet=EffectFacetClass.CREATE,
        verb="",
        lifetime=Lifetime.DURABLE,
        host_sections=frozenset(),
        release_timeout=None,
    )
    return dataclasses.replace(base, **changes)


def _codes(decl: Any) -> list[str]:
    return [r.code for r in check_declaration(decl)]


def _only(decl: Any) -> Any:
    refusals = check_declaration(decl)
    assert len(refusals) == 1, refusals
    return refusals[0]


def _entry(decl: Any) -> WorkflowEntry:
    return WorkflowEntry(root=decl.unit, units={decl.unit: decl}, deadline=timedelta(seconds=60))


# element name -> (declaration override, the same override as plugin source text)
MISSING: dict[str, tuple[dict[str, Any], str]] = {
    "preconditions": ({"preconditions": None}, "dict(preconditions=None)"),
    "postcondition": ({"postcondition": ""}, "dict(postcondition='')"),
    "wait": ({"wait": None}, "dict(wait=None)"),
    "resource_kind": ({"resource_kind": ""}, "dict(resource_kind='')"),
    "may_touch": ({"may_touch": None}, "dict(may_touch=None)"),
    "repeat": (
        {"flags": LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, None)},  # type: ignore[arg-type]
        "dict(flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, None))",
    ),
}


def _publish_refused(tmp_path: Path, overrides: str) -> PublicationRefused:
    """Through the real publication path: no snapshot is promoted, the stable code survives."""
    home = tmp_path / "home"
    path = tmp_path / "wf.py"
    path.write_text(_source(overrides), encoding="utf-8")
    with pytest.raises(PublicationRefused) as info:
        materialize_snapshot(path, "wf", home=home)
    snaps = home / "snapshots"
    assert not snaps.exists() or not any(snaps.iterdir())
    return info.value


def test_valid_declarations_pass_and_extract() -> None:
    assert check_declaration(fx.leaf_declaration()) == ()
    assert check_declaration(fx.all_declaration()) == ()
    assert check_declaration(fx.choice_declaration()) == ()
    assert extract_declared_tree(fx.leaf_entry()).root == "svc"  # identity path unchanged
    assert set(MISSING) == set(ELEMENTS)


@pytest.mark.proves(
    "WR-PLAN-12", "WR-PLAN-12:registration-six-elements", "A", "single", "LOGIC", "CI"
)
@pytest.mark.parametrize("element", ELEMENTS)
def test_missing_element_refused_names_element(element: str, tmp_path: Path) -> None:
    changes, source_override = MISSING[element]
    refusal = _only(_leaf(**changes))
    assert refusal.code == vocab.PLAN_CONTRACT_MISSING == "publication.plan_contract_missing"
    assert refusal.element == element and refusal.message.startswith(f"{element}:")
    with pytest.raises(ExtractionRefused) as extraction:
        extract_declared_tree(_entry(_leaf(**changes)))
    assert extraction.value.code == vocab.PLAN_CONTRACT_MISSING
    assert element in extraction.value.message
    # through publication: the stable code and the element, in the refusal a caller reads
    refused = _publish_refused(tmp_path, source_override)
    assert refused.code == vocab.PLAN_CONTRACT_MISSING
    assert f"{element}:" in str(refused)


def test_unusable_element_values_count_as_missing() -> None:
    zero = timedelta(0)
    for changes, element in (
        ({"postcondition": None}, "postcondition"),
        ({"resource_kind": None}, "resource_kind"),
        ({"preconditions": ("ok", "")}, "preconditions"),
        ({"may_touch": frozenset({""})}, "may_touch"),
        ({"wait": dataclasses.replace(fx.leaf_declaration().wait, max_wait=zero)}, "wait"),
        ({"wait": dataclasses.replace(fx.leaf_declaration().wait, poll_every=zero)}, "wait"),
    ):
        assert _only(_leaf(**changes)).element == element
    # an empty tuple is a declared "no preconditions", an empty touch scope a declared "none"
    assert check_declaration(_leaf(preconditions=(), may_touch=frozenset())) == ()


def test_safe_start_verb_outside_set() -> None:
    for verb in ("start", "refresh", "install"):
        ok = _effect(facet=EffectFacetClass.SAFE_START, verb=verb, lifetime=Lifetime.DURABLE)
        assert check_declaration(_leaf(effects=(ok,), remedies=())) == ()
    bad = _effect(facet=EffectFacetClass.SAFE_START, verb="restart", lifetime=Lifetime.DURABLE)
    refusal = _only(_leaf(effects=(bad,), remedies=()))
    assert refusal.code == vocab.SAFE_START_VERB_INVALID == "publication.safe_start_verb_invalid"
    assert refusal.element == "effects[e].verb"
    # only a SAFE_START effect's verb is checked
    other = _effect(facet=EffectFacetClass.OWNED, verb="restart", lifetime=Lifetime.DURABLE)
    assert check_declaration(_leaf(effects=(other,), remedies=())) == ()


def test_lifetime_facet_mismatch(tmp_path: Path) -> None:
    safe_run = _effect(facet=EffectFacetClass.SAFE_START, verb="start", lifetime=Lifetime.RUN)
    event_durable = _effect(facet=EffectFacetClass.EVENT, lifetime=Lifetime.DURABLE)
    for effect in (safe_run, event_durable):
        refusal = _only(_leaf(effects=(effect,), remedies=()))
        assert refusal.code == vocab.FACET_LIFETIME_MISMATCH
        assert refusal.element == "effects[e].lifetime"
    # the allowed pairings
    for facet, life in (
        (EffectFacetClass.EVENT, Lifetime.RUN),
        (EffectFacetClass.OWNED, Lifetime.RUN),
        (EffectFacetClass.OWNED, Lifetime.DURABLE),
    ):
        ok = _effect(facet=facet, lifetime=life)
        assert check_declaration(_leaf(effects=(ok,), remedies=())) == ()
    refused = _publish_refused(
        tmp_path,
        "dict(effects=(EffectDeclaration('e', EffectFacetClass.EVENT, '', Lifetime.DURABLE,"
        " frozenset(), None),))",
    )
    assert refused.code == vocab.FACET_LIFETIME_MISMATCH and "effects[e].lifetime" in str(refused)


def test_create_run_without_release_timeout(tmp_path: Path) -> None:
    for timeout in (None, timedelta(0)):
        bad = _effect(lifetime=Lifetime.RUN, release_timeout=timeout)
        refusal = _only(_leaf(effects=(bad,), remedies=()))
        assert (
            refusal.code == vocab.RELEASE_TIMEOUT_MISSING == "publication.release_timeout_missing"
        )
        assert refusal.element == "effects[e].release_timeout"
    ok = _effect(lifetime=Lifetime.RUN, release_timeout=timedelta(seconds=5))
    assert check_declaration(_leaf(effects=(ok,), remedies=())) == ()
    durable = _effect(lifetime=Lifetime.DURABLE, release_timeout=None)  # not a run resource
    assert check_declaration(_leaf(effects=(durable,), remedies=())) == ()
    refused = _publish_refused(
        tmp_path,
        "dict(effects=(EffectDeclaration('e', EffectFacetClass.CREATE, '', Lifetime.RUN,"
        " frozenset(), None),))",
    )
    assert refused.code == vocab.RELEASE_TIMEOUT_MISSING


@pytest.mark.parametrize("attempts", [0, -1, None, True, 1.5])
def test_max_attempts_lt_1(attempts: Any, tmp_path: Path) -> None:
    refusal = _only(_leaf(max_attempts=attempts))
    assert refusal.code == vocab.MAX_ATTEMPTS_INVALID == "publication.max_attempts_invalid"
    assert refusal.element == "max_attempts"
    assert check_declaration(_leaf(max_attempts=1)) == ()
    if attempts == 0:
        refused = _publish_refused(tmp_path, "dict(max_attempts=0)")
        assert refused.code == vocab.MAX_ATTEMPTS_INVALID


def test_release_effect_once(tmp_path: Path) -> None:
    release = _effect(effect="stop", facet=EffectFacetClass.OWNED, is_release=True)
    once = _flags(repeat=Repeat.ONCE)
    refusal = _only(_leaf(effects=(release,), remedies=(), flags=once))
    assert refusal.code == vocab.RELEASE_EFFECT_ONCE == "publication.release_effect_once"
    assert refusal.element == "effects[stop]"
    assert check_declaration(_leaf(effects=(release,), remedies=(), flags=_flags())) == ()
    # a ONCE leaf whose effects are not release effects is fine
    plain = _effect(facet=EffectFacetClass.OWNED)
    assert check_declaration(_leaf(effects=(plain,), remedies=(), flags=once)) == ()
    refused = _publish_refused(
        tmp_path,
        "dict(flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.ONCE),"
        " effects=(EffectDeclaration('stop', EffectFacetClass.OWNED, '', Lifetime.RUN,"
        " frozenset(), None, True),))",
    )
    assert refused.code == vocab.RELEASE_EFFECT_ONCE


def test_flags_contradict_type(tmp_path: Path) -> None:
    wrong = LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE)
    refusal = _only(_leaf(flags=wrong))
    assert refusal.code == vocab.FLAGS_CONTRADICT_TYPE == "publication.flags_contradict_type"
    assert refusal.element == "flags.compose"
    # a composite is observed and safe (V-14), and its compose must match its type
    all_decl = fx.all_declaration()
    recorded = dataclasses.replace(
        all_decl, flags=LoopFlags(Compose.ALL, CompletionSource.RECORDED, Repeat.SAFE)
    )
    assert _only(recorded).element == "flags.completion"
    once = dataclasses.replace(
        all_decl, flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.ONCE)
    )
    assert _only(once).element == "flags.repeat"
    choice = dataclasses.replace(
        fx.choice_declaration(),
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
    )
    assert _only(choice).element == "flags.compose"
    assert isinstance(all_decl, AllDeclaration)
    refused = _publish_refused(
        tmp_path, "dict(flags=LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE))"
    )
    assert refused.code == vocab.FLAGS_CONTRADICT_TYPE


def test_recorded_with_remedies(tmp_path: Path) -> None:
    recorded = _flags(completion=CompletionSource.RECORDED, repeat=Repeat.ONCE)
    remedy = RemedyDeclaration("tool.busy", "create_svc", 2, timedelta(seconds=20), timedelta(0))
    refusal = _only(_leaf(flags=recorded, remedies=(remedy,)))
    assert refusal.code == vocab.RECORDED_WITH_REMEDIES == "publication.recorded_with_remedies"
    assert refusal.element == "remedies"
    # its retryable codes are allowed (B1-E2); an observed leaf may declare remedies
    assert check_declaration(_leaf(flags=recorded, remedies=())) == ()
    assert check_declaration(_leaf(flags=_flags(), remedies=(remedy,))) == ()
    refused = _publish_refused(
        tmp_path,
        "dict(flags=LoopFlags(Compose.LEAF, CompletionSource.RECORDED, Repeat.ONCE),"
        " remedies=(RemedyDeclaration('tool.busy', 'e', 2, timedelta(seconds=20),"
        " timedelta(seconds=1)),))",
    )
    assert refused.code == vocab.RECORDED_WITH_REMEDIES


def test_refusals_are_ordered_and_all_reported() -> None:
    bad = _leaf(postcondition="", max_attempts=0, wait=None)
    assert _codes(bad) == [vocab.PLAN_CONTRACT_MISSING] * 2 + [vocab.MAX_ATTEMPTS_INVALID]
    assert [r.element for r in check_declaration(bad)] == ["postcondition", "wait", "max_attempts"]
    with pytest.raises(ExtractionRefused) as info:
        extract_declared_tree(_entry(bad))
    assert info.value.code == vocab.PLAN_CONTRACT_MISSING and "(+2 more)" in info.value.message


def test_check_does_not_mutate_or_import_plugin_state() -> None:
    decl = fx.leaf_declaration()
    before = repr(decl)
    check_declaration(decl)
    assert repr(decl) == before


def test_refused_publication_promotes_no_snapshot(tmp_path: Path) -> None:
    """Through the MCP publish_plugin call: the refusal names the element with its stable code,
    the previous snapshot keeps serving and a refused first publish leaves nothing to run."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = create_kernel(home=tmp_path / "trestle", plugin_dirs=[plugin_dir], skip_recovery=True)
    good = kernel.control.publish_plugin(_source(GOOD))
    assert isinstance(good, PublishView)
    snapshots = tmp_path / "trestle" / "snapshots"
    before = sorted(p.name for p in snapshots.iterdir())

    refused = kernel.control.publish_plugin(_source("dict(postcondition='')"))
    assert isinstance(refused, RequestOutcome)
    assert refused.code == vocab.PLAN_CONTRACT_MISSING == "publication.plan_contract_missing"
    assert refused.origin == "publication" and not refused.retryable
    assert "postcondition" in refused.message
    assert sorted(p.name for p in snapshots.iterdir()) == before  # nothing promoted
    desc = kernel.control.describe_plugin("wf")
    assert isinstance(desc, dict) and desc["snapshot_id"] == good.snapshot_id

    # a refused FIRST publication registers nothing: the plugin is not in the catalog
    other = _source("dict(max_attempts=0)").replace("def wf(", "def other(")
    first = kernel.control.publish_plugin(other)
    assert isinstance(first, RequestOutcome)
    assert first.code == vocab.MAX_ATTEMPTS_INVALID and "max_attempts" in first.message
    assert kernel.registry.get("other") is None
    assert sorted(p.name for p in snapshots.iterdir()) == before
