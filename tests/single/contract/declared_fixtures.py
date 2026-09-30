"""Declaration fixtures shared by the SV-2 contract tests and the SA-02 drift node."""

from __future__ import annotations

from datetime import timedelta

from trestle.workflow import (
    AllDeclaration,
    Alternative,
    ArgBinding,
    ChildBinding,
    ChoiceDeclaration,
    ChoiceNode,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    HostScopeRef,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    RemedyDeclaration,
    Repeat,
    Vantage,
    WaitPolicy,
    WorkflowEntry,
)

LEAF_FLAGS = LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE)
ALL_FLAGS = LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE)
CHOICE_FLAGS = LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE)


def leaf_declaration(unit: str = "svc", *, env_key_field: str | None = None) -> LeafDeclaration:
    return LeafDeclaration(
        unit=unit,
        flags=LEAF_FLAGS,
        preconditions=("net_up", "disk_free"),
        postcondition="svc_ready",
        wait=WaitPolicy(timedelta(seconds=2), 1.5, timedelta(seconds=60)),
        resource_kind="service",
        may_touch=frozenset({"service", "network", "volume"}),
        effects=(
            EffectDeclaration(
                effect="create_svc",
                facet=EffectFacetClass.CREATE,
                verb="",
                lifetime=Lifetime.RUN,
                host_sections=frozenset(
                    {HostScopeRef.TOOLCHAIN_INSTALLS, HostScopeRef.DEMO_CREDENTIAL}
                ),
                release_timeout=timedelta(seconds=30),
            ),
        ),
        retryable=frozenset({"tool.busy", "net.flaky"}),
        remedies=(
            RemedyDeclaration(
                code="tool.busy",
                effect="create_svc",
                attempts=2,
                total=timedelta(seconds=20),
                cooldown=timedelta(seconds=1),
            ),
        ),
        budget=timedelta(seconds=90),
        max_attempts=3,
        env_key_field=env_key_field,
    )


class FixtureLeaf:
    """A leaf unit: the loop calls `declare()`; observe/advance/release land with SV-5."""

    def __init__(self, unit: str = "svc", *, env_key_field: str | None = None) -> None:
        self._decl = leaf_declaration(unit, env_key_field=env_key_field)

    def declare(self) -> LeafDeclaration:
        return self._decl


class RaisingLeaf:
    def declare(self) -> LeafDeclaration:
        raise RuntimeError("boom")


def leaf_entry(unit: str = "svc", *, env_key_field: str | None = None) -> WorkflowEntry:
    return WorkflowEntry(
        root=unit,
        units={unit: FixtureLeaf(unit, env_key_field=env_key_field)},
        deadline=timedelta(seconds=300),
    )


def all_declaration() -> AllDeclaration:
    return AllDeclaration(
        unit="stack",
        flags=ALL_FLAGS,
        children=(
            ChildBinding(unit="db", params={"size": 1, "name": "$args.db"}, needs=()),
            ChildBinding(
                unit="api", params={}, needs=("db",), vantage=Vantage.CONTAINER, name="api1"
            ),
        ),
        concurrency=2,
        budget=timedelta(seconds=200),
        identifier_sets={"systems": frozenset({"db", "api"})},
        arg_bindings=(ArgBinding(arg="systems", identifier_set="systems", filters_children=True),),
        env_key_field="env",
        gates=("db",),
    )


def choice_declaration() -> ChoiceNode:
    return ChoiceNode(
        unit="pick",
        flags=CHOICE_FLAGS,
        choice=ChoiceDeclaration(
            logical_system="db",
            alternatives=(
                Alternative(
                    "db_docker", RealizationKind.DOCKER_SERVICE, frozenset({Vantage.HOST}), None
                ),
                Alternative(
                    "db_ide",
                    RealizationKind.EXTERNALLY_MANAGED,
                    frozenset({Vantage.HOST}),
                    "start it in the IDE",
                ),
            ),
            select_arg=None,
            fallback="db_docker",
            readiness="db_ready",
        ),
        budget=timedelta(seconds=120),
    )


def composite_entry() -> WorkflowEntry:
    return WorkflowEntry(
        root="stack",
        units={"stack": all_declaration(), "db": FixtureLeaf("db"), "api": FixtureLeaf("api")},
        deadline=timedelta(seconds=600),
    )
