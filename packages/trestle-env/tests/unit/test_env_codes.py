"""MC-B-12, environment half: declared codes only, never a V-11 meaning (L.RB-0.6)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from trestle.common.plan import precedence
from trestle.common.plan.bounds import CODE_MAX
from trestle.common.plan.vocabulary import NodeClass

from trestle_env import codes

VALUE = re.compile(r"^environment\.[a-z_]+$")

# interfaces/00-shared-vocabulary.md V-11: the closed set B may not extend (transcribed
# independently of the library vocabulary), and the leading word of each V-11 code that carries
# the meaning of an environment condition B4 lists (a name sharing it restates that meaning).
V11_NAMES = frozenset(
    """
    UNKNOWN_IDENTIFIER DECLARATION_CONFLICT DEPENDENCY_CYCLE UNIT_UNRESOLVED PLAN_CONTRACT_MISSING
    PLAN_PRECONDITION_UNCOVERED BOUND_EXCEEDED BUDGET_DOES_NOT_FIT LEASE_SET_UNDECIDABLE
    ROUTE_UNSUPPORTED DECLARATION_STALE EFFECT_UNCONFIRMED TICKET_REFUSED CANCEL_NOT_ROOT
    REALIZATION_ABSENT CREDENTIAL_INTERACTIVE CREDENTIAL_STALE CREDENTIAL_LIFETIME_INSUFFICIENT
    FOUND_UNHEALTHY FOUND_INCOMPATIBLE UNIT_RAISED POSTCONDITION_TIMEOUT PRECONDITION_UNSATISFIED
    LANE_UNAVAILABLE STOP_SEEN EXECUTION_CANCELLED EXECUTION_DEADLINE TOOLCHAIN_MISSING
    CLEANUP_UNKNOWN REMEDY_EXHAUSTED REMEDY_NO_PROGRESS CARVE_EXCEEDED CURRENCY_UNCONFIRMED
    SECTION_UNAVAILABLE DOCKER_CLI_MISSING DOCKER_ENGINE_UNREACHABLE TOOLCHAIN_INTERFACE_DRIFT
    ADOPTION_STALE COMPOSE_DEFINITION_INVALID GRANT_ISSUER_UNREACHABLE HOST_SCOPE_UNREADABLE
    WORKER_EXIT EXECUTION_RESTART VERTEX_UNENDED RESULT_UNENCODABLE
    PROVISION_STORE_UNREADABLE
    """.split()
)
ENVIRONMENT_CONDITION_STEMS = frozenset(
    {"unknown", "route", "postcondition", "found", "credential", "invalid", "duplicate"}
)


def environment_constants() -> dict[str, str]:
    return {
        name: value
        for name, value in vars(codes).items()
        if name.isupper() and isinstance(value, str)
    }


def problems(constants: dict[str, str], declared: frozenset[str]) -> list[str]:
    """Every way an environment constant can be a defect (empty when all are declared codes)."""
    found = []
    for name, value in constants.items():
        if not VALUE.match(value):
            found.append(f"{name}: {value!r} is not environment.<snake>")
            continue
        snake = value.split(".", 1)[1]
        if name != value.replace(".", "_").upper():
            found.append(f"{name}: NAME is not the upper-cased origin snake")
        if len(value) > CODE_MAX:
            found.append(f"{name}: longer than CODE_MAX")
        if snake.upper() in V11_NAMES or snake.split("_")[0] in ENVIRONMENT_CONDITION_STEMS:
            found.append(f"{name}: names a meaning a V-11 code already carries")
        if value not in declared:
            found.append(f"{name}: not declared by the reference workflow")
    return found


@dataclass(frozen=True)
class End:
    """The fields of V-4.8 `NodeEnd` that B4-T2 reads."""

    condition: str | None
    code: str | None
    cut: str | None = None
    provenance: str | None = None


def test_environment_codes_declared_not_v11() -> None:
    constants = environment_constants()
    assert constants == {"ENVIRONMENT_REPOSITORY_MISSING": "environment.repository_missing"}
    assert set(constants.values()) == set(codes.REFERENCE_WORKFLOW_CODES)
    assert problems(constants, codes.REFERENCE_WORKFLOW_CODES) == []

    # A planted `environment.found_foreign` (a foreign occupant is FOUND_INCOMPATIBLE) fails, as
    # do a wrong prefix, a NAME that is not the origin snake, an overlong code and a V-11 name.
    planted = {**constants, "ENVIRONMENT_FOUND_FOREIGN": "environment.found_foreign"}
    assert any(
        "found_foreign" in p or "FOUND_FOREIGN" in p
        for p in problems(planted, codes.REFERENCE_WORKFLOW_CODES)
    )
    for name, value in (
        ("FOUND_INCOMPATIBLE", "environment.found_incompatible"),
        ("ROUTE_UNSUPPORTED", "environment.route_unsupported"),
        ("BAD", "execution.repository_missing"),
        ("REPOSITORY_MISSING", "environment.repository_missing"),
        ("ENVIRONMENT_" + "X" * CODE_MAX, "environment." + "x" * CODE_MAX),
    ):
        assert problems({name: value}, frozenset({value})), (name, value)

    # B4-T2 row 9: a declared code on a `Blocked` NodeEnd is class BLOCKED, and it is no row's code.
    for value in constants.values():
        assert precedence.node_class(End(condition="blocked", code=value)) is NodeClass.BLOCKED
        assert all(
            precedence._v11_name(value) not in row.codes for row in precedence.NODE_CLASS_ROWS
        )
