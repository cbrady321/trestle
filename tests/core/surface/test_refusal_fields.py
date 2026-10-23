"""Change 2b: `retry_after_s` on every retryable outcome, `held_by` and `free_at` on
`admission.environment_busy` (docs/proposals/2026-10-22-agent-surface-2a-2d-design.md)."""

from __future__ import annotations

import ast
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

import trestle
from trestle.common import codes
from trestle.common.types import RequestOutcome
from trestle.server import pool as pools
from trestle.server.admission import _environment_busy_refusal

ROOT = Path(trestle.__file__).resolve().parent


@pytest.mark.parametrize("code", sorted(codes.RETRY_AFTER_S))
def test_every_table_code_round_trips_as_an_int(code: str) -> None:
    out = RequestOutcome(code=code, message="m", retryable=True, origin="admission").to_dict()
    assert out["retry_after_s"] == codes.RETRY_AFTER_S[code]
    assert type(out["retry_after_s"]) is int


def test_unknown_retryable_code_gets_the_default() -> None:
    out = RequestOutcome(code="x.unknown", message="m", retryable=True, origin="projection")
    assert out.to_dict()["retry_after_s"] == codes.DEFAULT_RETRY_AFTER_S


def test_own_value_wins_over_the_table() -> None:
    out = RequestOutcome(
        code=codes.QUEUE_FULL, message="m", retryable=True, origin="admission", retry_after_s=42
    )
    assert out.to_dict()["retry_after_s"] == 42


def test_non_retryable_outcome_carries_none_of_the_fields() -> None:
    out = RequestOutcome(
        code=codes.PLUGIN_NOT_FOUND, message="m", retryable=False, origin="admission"
    ).to_dict()
    assert set(out) == {"code", "message", "retryable", "origin"}


def _retryable_codes_in_source() -> set[str]:
    """The `code=` of every `RequestOutcome(..., retryable=True, ...)` in trestle/."""
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "RequestOutcome":
                continue
            kw = {k.arg: k.value for k in node.keywords}
            retry = kw.get("retryable")
            if not (isinstance(retry, ast.Constant) and retry.value is True):
                continue
            code = kw.get("code")
            assert isinstance(code, ast.Attribute), f"{path}: retryable code is not codes.X"
            found.add(getattr(codes, code.attr))
    return found


def test_every_retryable_site_is_in_the_table() -> None:
    found = _retryable_codes_in_source()
    assert found, "the scan found no retryable site"
    assert found <= set(codes.RETRY_AFTER_S), found - set(codes.RETRY_AFTER_S)
    assert found == set(codes.RETRY_AFTER_S)


def test_environment_busy_arithmetic_and_fields() -> None:
    now = 1_000_000.0
    free_at = now + 100.5
    # worst case + release slice - deadline = -60: the request fits 40.5 s from now, so 41
    refused = _environment_busy_refusal("r_holder", free_at, -60.0, now)
    out = refused.outcome.to_dict()
    assert out["code"] == codes.ADMISSION_ENVIRONMENT_BUSY
    assert out["retryable"] is True
    assert out["retry_after_s"] == math.ceil(100.5 - 60.0) == 41
    assert out["held_by"] == "r_holder"
    assert out["free_at"] == datetime.fromtimestamp(free_at, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert "r_holder" in out["message"] and "resend in 41s" in out["message"]
    assert len(out["message"]) <= 200
    # never below 1
    assert _environment_busy_refusal("r", now + 1, -500.0, now).outcome.retry_after_s == 1


def _state() -> pools.State:
    state = pools.empty_state()
    state["running"]["r_a"] = {"server": "s1", "key": "k", "deadline": 200.0}
    state["running"]["r_other"] = {"server": "s1", "key": "j", "deadline": 900.0}
    state["waiting_keys"]["k"] = [
        {"run_id": "r_b", "server": "s1", "arrival": 10.0, "deadline": 300.0},
        {"run_id": "r_c", "server": "s1", "arrival": 20.0, "deadline": 300.0},
    ]
    return state


def test_busy_holder_is_the_latest_deadline_latest_arrival() -> None:
    state = _state()
    assert pools.busy_holder(state, "k", now=0.0) == ("r_c", 300.0)
    assert pools.busy_holder(state, "j", now=0.0) == ("r_other", 900.0)
    assert pools.busy_holder(state, "none", now=0.0) is None
    # past every deadline: free
    assert pools.busy_holder(state, "k", now=301.0) is None
