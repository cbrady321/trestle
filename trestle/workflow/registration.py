"""Registration refusals (B1-E1/B1-E2, single-vertex set; L.SL-7.1).

`check_declaration(decl)` returns every reason a declaration must not be registered, each with
its stable code (`trestle.common.plan.vocabulary`, the single-level set, and the element it names).
Publication (`extract_declared_tree`) refuses on the first one, so nothing is snapshotted and no
run can start (B1-E1: "at publication, before any run").

The check reads declaration data only. It never imports a plugin, reads a clock or calls a port,
and never mutates its argument. An element counts as *missing* when the value the plugin bound is
absent or unusable (`None`, the wrong type, an empty name, a non-positive duration): the frozen
declaration types do not validate, so a plugin can bind any of these.

L.SL-2.1 adds the in-node stage budgets (`_budget_refusals`) and L.SL-6.2 the remedy ownership
boundary (`_remedy_refusals`, B1-E3); each appends to the returned tuple in a fixed order, so the
first refusal is deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import cast, final

from trestle.common.plan import vocabulary as vocab
from trestle.workflow.declarations import (
    AllDeclaration,
    ChoiceNode,
    CompletionSource,
    Compose,
    Declaration,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
)

# B1-E1: a SAFE_START effect is a start, a refresh or an install, never anything else.
SAFE_START_VERBS: frozenset[str] = frozenset({"start", "refresh", "install"})

# The six WR-PLAN-12 elements, in the order they are checked and named.
ELEMENTS: tuple[str, ...] = (
    "preconditions",
    "postcondition",
    "wait",
    "resource_kind",
    "may_touch",
    "repeat",
)


@final
@dataclass(frozen=True, slots=True)
class RegistrationRefusal:
    """One refusal: the stable `code`, the declared `element` it names, a short `message`."""

    code: str
    element: str
    message: str


def _positive(value: object) -> bool:
    return isinstance(value, timedelta) and value > timedelta(0)


def _names(value: object, kinds: tuple[type, ...]) -> bool:
    """A collection of non-empty strings (an empty collection is a declared 'none')."""
    return isinstance(value, kinds) and all(
        isinstance(v, str) and v for v in cast(Iterable[object], value)
    )


def _missing(element: str, why: str) -> RegistrationRefusal:
    return RegistrationRefusal(vocab.PLAN_CONTRACT_MISSING, element, f"{element}: {why}")


def _flag_refusals(decl: Declaration) -> list[RegistrationRefusal]:
    flags = decl.flags
    if not isinstance(flags, LoopFlags):
        return [RegistrationRefusal(vocab.FLAGS_CONTRADICT_TYPE, "flags", "flags: not LoopFlags")]
    expected = (
        Compose.LEAF
        if isinstance(decl, LeafDeclaration)
        else Compose.ALL
        if isinstance(decl, AllDeclaration)
        else Compose.CHOICE
    )
    out: list[RegistrationRefusal] = []
    if flags.compose != expected:
        out.append(
            RegistrationRefusal(
                vocab.FLAGS_CONTRADICT_TYPE,
                "flags.compose",
                f"flags.compose: {expected.value} declaration carries {flags.compose!s}",
            )
        )
    if expected is not Compose.LEAF:  # V-14: a composite is OBSERVED and SAFE
        if flags.completion != CompletionSource.OBSERVED:
            out.append(
                RegistrationRefusal(
                    vocab.FLAGS_CONTRADICT_TYPE,
                    "flags.completion",
                    f"flags.completion: a {expected.value} composite must be observed",
                )
            )
        if flags.repeat != Repeat.SAFE:
            out.append(
                RegistrationRefusal(
                    vocab.FLAGS_CONTRADICT_TYPE,
                    "flags.repeat",
                    f"flags.repeat: a {expected.value} composite must be safe",
                )
            )
    return out


def _six_elements(decl: LeafDeclaration) -> list[RegistrationRefusal]:
    out: list[RegistrationRefusal] = []
    if not _names(decl.preconditions, (tuple, list)):
        out.append(_missing("preconditions", "not declared (a tuple of check names; () for none)"))
    if not (isinstance(decl.postcondition, str) and decl.postcondition):
        out.append(_missing("postcondition", "no completion check declared"))
    wait = decl.wait
    if not (
        isinstance(wait, WaitPolicy) and _positive(wait.poll_every) and _positive(wait.max_wait)
    ):
        out.append(_missing("wait", "no bounded wait policy (poll_every and max_wait > 0)"))
    if not (isinstance(decl.resource_kind, str) and decl.resource_kind):
        out.append(_missing("resource_kind", "no resource kind declared"))
    if not _names(decl.may_touch, (frozenset, set)):
        out.append(_missing("may_touch", "no touch scope declared (a set of resource kinds)"))
    flags = decl.flags
    if isinstance(flags, LoopFlags) and not isinstance(flags.repeat, Repeat):
        out.append(_missing("repeat", "repeat-safety not declared (flags.repeat)"))
    return out


def _effect_refusals(decl: LeafDeclaration) -> list[RegistrationRefusal]:
    out: list[RegistrationRefusal] = []
    once = isinstance(decl.flags, LoopFlags) and decl.flags.repeat == Repeat.ONCE
    effects = decl.effects if isinstance(decl.effects, (tuple, list)) else ()
    for eff in effects:
        if not isinstance(eff, EffectDeclaration):
            continue
        where = f"effects[{eff.effect}]"
        if eff.facet == EffectFacetClass.SAFE_START and eff.verb not in SAFE_START_VERBS:
            out.append(
                RegistrationRefusal(
                    vocab.SAFE_START_VERB_INVALID,
                    f"{where}.verb",
                    f"{where}.verb: {eff.verb!r} is not start, refresh or install",
                )
            )
        if eff.facet == EffectFacetClass.SAFE_START and eff.lifetime == Lifetime.RUN:
            out.append(
                RegistrationRefusal(
                    vocab.FACET_LIFETIME_MISMATCH,
                    f"{where}.lifetime",
                    f"{where}.lifetime: a safe_start effect cannot be run-lifetime",
                )
            )
        if eff.facet == EffectFacetClass.EVENT and eff.lifetime == Lifetime.DURABLE:
            out.append(
                RegistrationRefusal(
                    vocab.FACET_LIFETIME_MISMATCH,
                    f"{where}.lifetime",
                    f"{where}.lifetime: an event effect cannot be durable",
                )
            )
        if (
            eff.facet == EffectFacetClass.CREATE
            and eff.lifetime == Lifetime.RUN
            and not _positive(eff.release_timeout)
        ):
            out.append(
                RegistrationRefusal(
                    vocab.RELEASE_TIMEOUT_MISSING,
                    f"{where}.release_timeout",
                    f"{where}.release_timeout: a run-lifetime create needs a release timeout",
                )
            )
        if eff.is_release and once:
            out.append(
                RegistrationRefusal(
                    vocab.RELEASE_EFFECT_ONCE,
                    where,
                    f"{where}: a release effect cannot be declared once (flags.repeat)",
                )
            )
    return out


def _seconds(value: object) -> float | None:
    """A usable duration in seconds, else None (a missing element is `_six_elements`' refusal)."""
    return value.total_seconds() if isinstance(value, timedelta) else None


def stage_budget_s(decl: LeafDeclaration) -> float | None:
    """What a leaf's own stages can take inside its budget, in seconds (B2-C5, L.SL-2.1): the
    wait (`wait.max_wait`) plus every remedy's `total` plus the longest release timeout of the
    CREATE + RUN effects it declares (the same effects and timeouts `carving.margin_needed`
    reads). None when a stage value is unusable: that is another refusal's ground."""
    wait = decl.wait
    waited = _seconds(wait.max_wait) if isinstance(wait, WaitPolicy) else None
    if waited is None:
        return None
    remedies = decl.remedies if isinstance(decl.remedies, (tuple, list)) else ()
    remedy_total = 0.0
    for remedy in remedies:
        total = _seconds(getattr(remedy, "total", None))
        if total is None:
            return None
        remedy_total += total
    effects = decl.effects if isinstance(decl.effects, (tuple, list)) else ()
    release = 0.0
    for eff in effects:
        if (
            isinstance(eff, EffectDeclaration)
            and eff.facet == EffectFacetClass.CREATE
            and eff.lifetime == Lifetime.RUN
        ):
            timeout = _seconds(eff.release_timeout)
            if timeout is not None:  # a missing one is RELEASE_TIMEOUT_MISSING
                release = max(release, timeout)
    return waited + remedy_total + release


def _budget_refusals(decl: LeafDeclaration) -> list[RegistrationRefusal]:
    """B2-C5 in the leaf: the stages it declares must fit the budget it declares, so an
    over-budget leaf never reaches a run (publication.budget_exceeds_leaf)."""
    budget = _seconds(decl.budget)
    needed = stage_budget_s(decl)
    if budget is None or needed is None or needed <= budget:
        return []
    return [
        RegistrationRefusal(
            vocab.BUDGET_EXCEEDS_LEAF,
            "budget",
            f"budget: wait + remedies + release timeout need {needed:g}s, over the leaf's "
            f"{budget:g}s budget",
        )
    ]


def _remedy_refusals(decl: LeafDeclaration) -> list[RegistrationRefusal]:
    """B1-E3 (WR-REMEDY-4, WR-OWN-10): an `OWNED` remedy is authorized only against `state.owned`,
    the handles this node's own CREATE tickets yielded. A leaf that declares no CREATE effect owns
    nothing (its resource is found, and a found resource is never adopted: V-4.2), so an OWNED
    remedy on it can never be issued: it is refused here, at publication, as
    `publication.owned_remedy_on_found`. A remedy that changes a found resource must be a
    `SAFE_START` effect, whose verb `_effect_refusals` already holds to start/refresh/install."""
    effects = (
        tuple(e for e in decl.effects if isinstance(e, EffectDeclaration))
        if isinstance(decl.effects, (tuple, list))
        else ()
    )
    if any(e.facet == EffectFacetClass.CREATE for e in effects):
        return []
    by_id = {e.effect: e for e in effects}
    remedies = decl.remedies if isinstance(decl.remedies, (tuple, list)) else ()
    out: list[RegistrationRefusal] = []
    for remedy in remedies:
        if not isinstance(remedy, RemedyDeclaration):
            continue
        target = by_id.get(remedy.effect)
        if target is None or target.facet != EffectFacetClass.OWNED:
            continue  # undeclared: a runtime UNDECLARED_EFFECT; other facets: not an owned change
        where = f"remedies[{remedy.code}]"
        out.append(
            RegistrationRefusal(
                vocab.OWNED_REMEDY_ON_FOUND,
                f"{where}.effect",
                f"{where}.effect: {remedy.effect!r} is an owned effect on a leaf that creates "
                "nothing; a found resource is only changed by a safe_start (start, refresh, "
                "install)",
            )
        )
    return out


def _leaf_refusals(decl: LeafDeclaration) -> list[RegistrationRefusal]:
    out = _six_elements(decl)
    out += _effect_refusals(decl)
    attempts = decl.max_attempts
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        out.append(
            RegistrationRefusal(
                vocab.MAX_ATTEMPTS_INVALID, "max_attempts", "max_attempts: must be an int >= 1"
            )
        )
    flags = decl.flags
    if (
        isinstance(flags, LoopFlags)
        and flags.completion == CompletionSource.RECORDED
        and decl.remedies
    ):
        out.append(
            RegistrationRefusal(
                vocab.RECORDED_WITH_REMEDIES,
                "remedies",
                "remedies: a recorded leaf cannot declare remedies",
            )
        )
    out += _budget_refusals(decl)
    out += _remedy_refusals(decl)
    return out


def check_declaration(decl: Declaration) -> tuple[RegistrationRefusal, ...]:
    """Every registration refusal for `decl`, in a fixed order (flags, the six elements, effects,
    attempts, recorded-with-remedies, stage budgets, remedy ownership). Empty means it may be
    registered."""
    if not isinstance(decl, (LeafDeclaration, AllDeclaration, ChoiceNode)):
        return ()  # not a declaration: extraction refuses it as such
    out = _flag_refusals(decl)
    if isinstance(decl, LeafDeclaration):
        out += _leaf_refusals(decl)
    return tuple(out)
