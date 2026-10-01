"""L.NW-2.3 (MC-B-12, adapter half): every adapter code constant is a V-11 code, spelled as DM-16
spells it, in the vocabulary, re-exported by `trestle.common.codes` and mirrored, value for value,
in the adapter package (which imports only stdlib and `trestle.workflow`)."""

from __future__ import annotations

import re
from collections.abc import Mapping

import pytest
from trestle.common import codes
from trestle.common.plan import bounds
from trestle.common.plan import vocabulary as vocab
from trestle.workflow import codes as workflow_codes

from trestle_packs.container import engine

# interfaces/00-shared-vocabulary.md V-11 ("The set is closed"), transcribed name by name.
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

# MC-B-12, adapter half (b-slice-b.md L.NW-2.3): the codes B's adapters raise. The eighth is the
# one V-11 name the transcription above gains (L.RB-6.3.fix1, ADD-code-provision-store-unreadable).
ADAPTER_NAMES = (
    "DOCKER_CLI_MISSING",
    "DOCKER_ENGINE_UNREACHABLE",
    "TOOLCHAIN_MISSING",
    "TOOLCHAIN_INTERFACE_DRIFT",
    "COMPOSE_DEFINITION_INVALID",
    "CREDENTIAL_INTERACTIVE",
    "GRANT_ISSUER_UNREACHABLE",
    "PROVISION_STORE_UNREADABLE",
)
# V-11 gives every code ONE spelling. A-1 already spelled these two `execution.*` (the join's
# human-actionable set J-5a and the real command port compare that value), so they keep it.
A1_SPELLED = {
    "TOOLCHAIN_MISSING": "execution.toolchain_missing",
    "CREDENTIAL_INTERACTIVE": "execution.credential_interactive",
}
VALUE = re.compile(r"^(adapter|execution)\.[a-z_]+$")


def snake(name: str) -> str:
    return name.lower()


def problems(constants: Mapping[str, str]) -> list[str]:
    """Why a set of adapter constants is not a set of V-11 codes."""
    found: list[str] = []
    for name, value in constants.items():
        if name not in V11_NAMES:
            found.append(f"{name}: not a V-11 code name")
        if not VALUE.match(value) or value.split(".", 1)[1] != snake(name):
            found.append(f"{name}: value {value!r} is not <origin>.{snake(name)}")
        if name in A1_SPELLED and value != A1_SPELLED[name]:
            found.append(f"{name}: A-1 spells it {A1_SPELLED[name]!r}")
        if name not in A1_SPELLED and not value.startswith("adapter."):
            found.append(f"{name}: an adapter-raised code is adapter.<snake>")
    return found


def test_adapter_codes_are_v11_codes() -> None:
    constants = {name: getattr(vocab, name) for name in ADAPTER_NAMES}
    assert problems(constants) == []
    assert set(constants.values()) == vocab.ADAPTER_CODES
    assert len(set(constants.values())) == len(ADAPTER_NAMES)


def test_adapter_constants_carry_the_v11_name_and_dm16_value() -> None:
    assert vocab.DOCKER_CLI_MISSING == "adapter.docker_cli_missing"  # the plan's own example
    assert vocab.DOCKER_ENGINE_UNREACHABLE == "adapter.docker_engine_unreachable"
    for name in ADAPTER_NAMES:
        assert getattr(vocab, name) == getattr(codes, name), name  # re-exported unchanged


def test_the_provision_store_code_is_the_one_added_v11_name() -> None:
    """L.RB-6.3.fix1: V-11 has no code for a record store that did not answer; the adapter half
    gains `PROVISION_STORE_UNREADABLE`, spelled and mirrored like the seven others."""
    assert vocab.PROVISION_STORE_UNREADABLE == "adapter.provision_store_unreadable"
    assert codes.PROVISION_STORE_UNREADABLE == vocab.PROVISION_STORE_UNREADABLE
    assert engine.PROVISION_STORE_UNREADABLE == vocab.PROVISION_STORE_UNREADABLE
    assert vocab.PROVISION_STORE_UNREADABLE in vocab.ADAPTER_CODES
    assert vocab.PROVISION_STORE_UNREADABLE not in {
        vocab.DOCKER_ENGINE_UNREACHABLE,  # the engine answered; the store did not
        vocab.TOOLCHAIN_INTERFACE_DRIFT,  # an unexpected output shape is another failure
    }


def test_a_planted_constant_outside_v11_fails() -> None:
    assert problems({"DOCKER_SOCKET_BUSY": "adapter.docker_socket_busy"}) != []
    assert problems({"DOCKER_CLI_MISSING": "adapter.docker_cli_gone"}) != []  # NAME != value
    assert problems({"DOCKER_CLI_MISSING": "docker.cli_missing"}) != []  # not <origin>.<snake>
    assert problems({"TOOLCHAIN_MISSING": "adapter.toolchain_missing"}) != []  # A-1's spelling
    assert problems({"DOCKER_ENGINE_UNREACHABLE": "execution.docker_engine_unreachable"}) != []


def test_a1_spelled_codes_agree_with_the_workflow_package() -> None:
    assert vocab.TOOLCHAIN_MISSING == workflow_codes.TOOLCHAIN_MISSING
    assert vocab.CREDENTIAL_INTERACTIVE == workflow_codes.CREDENTIAL_INTERACTIVE
    assert workflow_codes.HUMAN_ACTIONABLE == {
        vocab.TOOLCHAIN_MISSING,
        vocab.CREDENTIAL_INTERACTIVE,
    }


def test_the_adapter_package_mirrors_the_vocabulary() -> None:
    for name in ADAPTER_NAMES:
        assert getattr(engine, name) == getattr(vocab, name), name
    assert engine.ADAPTER_CODES == vocab.ADAPTER_CODES


def test_adapter_codes_stay_out_of_the_other_code_sets() -> None:
    # they are not the single-level nor plan-refusal sets, and EXECUTION_CODES is unchanged
    assert not vocab.ADAPTER_CODES & vocab.PLAN_REFUSAL_CODES
    assert not (vocab.ADAPTER_CODES & vocab.SINGLE_LEVEL_CODES)
    assert not (vocab.ADAPTER_CODES - {vocab.TOOLCHAIN_MISSING, vocab.CREDENTIAL_INTERACTIVE}) & (
        codes.EXECUTION_CODES
    )


@pytest.mark.parametrize("name", ADAPTER_NAMES)
def test_every_adapter_code_is_within_code_max(name: str) -> None:
    assert len(getattr(vocab, name)) <= bounds.CODE_MAX  # V-13
