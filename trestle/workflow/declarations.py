"""Declaration data (MC-24, declaration half; L.SV-2.1).

Frozen, slotted, stdlib-only transcriptions of the vocabulary's V-6 (`LoopFlags` and its three
enums), V-7 (`Alternative`, `ChoiceDeclaration` and their enums), V-9 (`HostScopeRef`) and V-14
(declaration data), plus B1-C8's `WorkflowEntry`. Pure data: no method, no default beyond the
ones the contract writes, no bound check (V-13 bounds are enforced at publication and
admission, not by these types). This module imports nothing from `trestle.server`,
`trestle.child`, `trestle.query`, `trestle.ops` or `trestle.wrapper` (C.5 step 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import final

# V-0 primitive aliases (static strings; bounds in V-13).
type UnitRef = str
type EffectId = str
type CheckRef = str
type ResourceKind = str
type ArgPath = str
type SetName = str
type StableCode = str
type HumanAction = str
type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


# ---- V-6


class Compose(StrEnum):
    LEAF = "leaf"
    ALL = "all"
    CHOICE = "choice"


class CompletionSource(StrEnum):
    OBSERVED = "observed"
    RECORDED = "recorded"


class Repeat(StrEnum):
    SAFE = "safe"
    ONCE = "once"


@final
@dataclass(frozen=True, slots=True)
class LoopFlags:
    compose: Compose
    completion: CompletionSource
    repeat: Repeat


# ---- V-4 / V-7 / V-9 enums


class Lifetime(StrEnum):
    RUN = "run"
    DURABLE = "durable"


class RealizationKind(StrEnum):
    DOCKER_SERVICE = "docker_service"
    AGENT_LAUNCHED_PROJECT = "agent_launched_project"
    EXTERNALLY_MANAGED = "externally_managed"
    PROVISIONED = "provisioned"


class Vantage(StrEnum):
    HOST = "host"
    CONTAINER = "container"


class HostScopeRef(StrEnum):
    DEMO_CREDENTIAL = "demo_credential"
    TOOLCHAIN_INSTALLS = "toolchain_installs"


@final
@dataclass(frozen=True, slots=True)
class Alternative:
    unit: UnitRef
    realization: RealizationKind
    reachable_from: frozenset[Vantage]
    human_action: HumanAction | None


@final
@dataclass(frozen=True, slots=True)
class ChoiceDeclaration:
    logical_system: str
    alternatives: tuple[Alternative, ...]
    select_arg: ArgPath | None
    fallback: UnitRef | None
    readiness: CheckRef


# ---- V-14


class EffectFacetClass(StrEnum):
    CREATE = "create"
    OWNED = "owned"
    SAFE_START = "safe_start"
    EVENT = "event"


@final
@dataclass(frozen=True, slots=True)
class WaitPolicy:
    poll_every: timedelta
    backoff: float
    max_wait: timedelta


@final
@dataclass(frozen=True, slots=True)
class EffectDeclaration:
    effect: EffectId
    facet: EffectFacetClass
    verb: str
    lifetime: Lifetime
    host_sections: frozenset[HostScopeRef]
    release_timeout: timedelta | None
    is_release: bool = False


@final
@dataclass(frozen=True, slots=True)
class RemedyDeclaration:
    code: StableCode
    effect: EffectId
    attempts: int
    total: timedelta
    cooldown: timedelta


@final
@dataclass(frozen=True, slots=True)
class LeafDeclaration:
    unit: UnitRef
    flags: LoopFlags
    preconditions: tuple[CheckRef, ...]
    postcondition: CheckRef
    wait: WaitPolicy
    resource_kind: ResourceKind
    may_touch: frozenset[ResourceKind]
    effects: tuple[EffectDeclaration, ...]
    retryable: frozenset[StableCode]
    remedies: tuple[RemedyDeclaration, ...]
    budget: timedelta
    max_attempts: int
    env_key_field: ArgPath | None = None


@final
@dataclass(frozen=True, slots=True)
class ChildBinding:
    unit: UnitRef
    params: Mapping[str, JsonValue | ArgPath]
    needs: tuple[str, ...]
    vantage: Vantage = Vantage.HOST
    name: str | None = None


@final
@dataclass(frozen=True, slots=True)
class ArgBinding:
    arg: ArgPath
    identifier_set: SetName
    filters_children: bool


@final
@dataclass(frozen=True, slots=True)
class AllDeclaration:
    unit: UnitRef
    flags: LoopFlags
    children: tuple[ChildBinding, ...]
    concurrency: int
    budget: timedelta
    identifier_sets: Mapping[SetName, frozenset[str]]
    arg_bindings: tuple[ArgBinding, ...]
    env_key_field: ArgPath | None
    gates: tuple[str, ...] = ()


@final
@dataclass(frozen=True, slots=True)
class ChoiceNode:
    unit: UnitRef
    flags: LoopFlags
    choice: ChoiceDeclaration
    budget: timedelta


type Declaration = LeafDeclaration | AllDeclaration | ChoiceNode


# ---- B1-C8


@final
@dataclass(frozen=True, slots=True)
class WorkflowEntry:
    """Names the root and every unit reachable from it, statically (B1-C8).

    `units` maps a unit name to its `WorkUnit` (a leaf: the loop calls `declare()`), or to an
    `AllDeclaration` / `ChoiceNode` (a composite is pure data). The `WorkUnit` protocol is
    B1-C1's and lands with the loop's contract types (L.SV-5.3), so it is typed `object` here.
    """

    root: UnitRef
    units: Mapping[UnitRef, object]
    deadline: timedelta
