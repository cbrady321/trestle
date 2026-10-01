"""SA-03 for Slice B (L.NW-2.3): the B code set is V-11's, and no code carries a class of its own.

Every B code constant is a V-11 code or a code the reference workflow declares (V-11 is a closed
set; B3-E1, MC-B-12), and B4-T2 decides the class of the `NodeEnd` it is raised under: an adapter
code rides a `Blocked` step, so the vertex ends `blocked` and B4-T2's row 9 gives `BLOCKED`; the
one OQ-32 variant (`EXHAUSTED` answers `BLOCKED`, B4-T3) is the only switch. The test iterates
whatever B codes exist: the adapter half (`trestle.common.plan.vocabulary.ADAPTER_CODES`) and, once
L.RB-0.6 lands it, the environment half (`trestle_env.codes`), so RB-0.6's additions are covered
without an edit here.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass

import pytest

from trestle.common.outcome import OutcomeClass
from trestle.common.plan import precedence
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.vocabulary import NodeClass

# interfaces/00-shared-vocabulary.md V-11, transcribed independently of the packs test.
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
# Codes the reference workflow itself declares (MC-B-12, environment half): never V-11 names.
REFERENCE_WORKFLOW_CODES = frozenset({"ENVIRONMENT_REPOSITORY_MISSING"})
VALUE = re.compile(r"^[a-z]+\.[a-z_]+$")


def b_codes() -> dict[str, str]:
    """NAME -> value of every B code that exists on this checkout."""
    found = {
        name: getattr(vocab, name)
        for name in dir(vocab)
        if name.isupper()
        and isinstance(getattr(vocab, name), str)
        and getattr(vocab, name) in vocab.ADAPTER_CODES
    }
    try:  # L.RB-0.6's environment half; absent before it lands
        env = importlib.import_module("trestle_env.codes")
    except ModuleNotFoundError:
        env = None
    if env is not None:
        found.update(
            {
                n: getattr(env, n)
                for n in dir(env)
                if n.isupper() and isinstance(getattr(env, n), str)
            }
        )
    return found


@dataclass(frozen=True)
class End:
    """The fields of V-4.8 `NodeEnd` that B4-T2 reads."""

    condition: str | None
    code: str | None
    cut: str | None = None
    provenance: str | None = None


@pytest.mark.parametrize("sa", ["SA-03"])
def test_every_b_code_is_v11_or_declared_by_the_reference_workflow(sa: str) -> None:
    codes = b_codes()
    assert set(codes.values()) >= vocab.ADAPTER_CODES  # the adapter half is all there
    for name, value in codes.items():
        assert VALUE.match(value), (name, value)
        origin, snake = value.split(".", 1)
        # NAME is the upper of the snake, origin-qualified for the environment half (MC-B-12,
        # L.RB-0.6: `environment.repository_missing` is `ENVIRONMENT_REPOSITORY_MISSING`)
        assert name.lower() == (f"{origin}_{snake}" if origin == "environment" else snake), (
            name,
            value,
        )
        assert name in V11_NAMES | REFERENCE_WORKFLOW_CODES, f"{name} is outside V-11 (B3-E1)"


@pytest.mark.parametrize("sa", ["SA-03"])
def test_a_code_outside_v11_is_detected(sa: str) -> None:
    assert "DOCKER_SOCKET_BUSY" not in V11_NAMES | REFERENCE_WORKFLOW_CODES


@pytest.mark.parametrize("sa", ["SA-03"])
def test_b4_t2_gives_every_b_code_the_class_of_its_node_end(sa: str) -> None:
    for name, value in b_codes().items():
        blocked = End(condition="blocked", code=value)
        assert precedence.node_class(blocked) is NodeClass.BLOCKED, name
        # no B code appears as a row code of B4-T2: a code carries no class of its own
        assert all(
            precedence._v11_name(value) not in row.codes for row in precedence.NODE_CLASS_ROWS
        )


@pytest.mark.parametrize("sa", ["SA-03"])
def test_the_one_variant_exhausted_answers_blocked(sa: str) -> None:
    assert precedence.EXHAUSTED_AS is OutcomeClass.BLOCKED
    assert precedence.outcome_of(NodeClass.EXHAUSTED) is OutcomeClass.BLOCKED
    assert precedence.outcome_of(NodeClass.BLOCKED) is OutcomeClass.BLOCKED
