"""G-E2 on pull_request runs (owner decision 2026-10-02, FINAL-REPORT section 3 item 4): a head
with no host-docker record YET is pending (the target skips), so `test` can succeed and
proof-ledger and the CK drills run before the host session. A record that exists and fails is a
failure on every run, and a run that is not a pull_request (push to master, workflow_dispatch, a
local run) still fails without the record. Landing enforces the record whatever G-E2 reported
(fence rule L1, tests/proof/selftest/test_fence_merge.py `test_l1_*`)."""

from __future__ import annotations

import pytest

from tests.pins.e_packs import test_g_e2 as g_e2
from tests.proof.host import record as record_mod
from tests.proof.markers import TargetUnmet

NONE = record_mod.NO_HOST_DOCKER_RECORD
FAILING = "host-docker record for HEAD is run/FAILED, not a passing run"
NOT_LIVE = "the live compose node is not PASSED in the record"


@pytest.mark.parametrize(
    ("problem", "event", "pending"),
    [
        (NONE, "pull_request", True),
        (NONE, "push", False),
        (NONE, "workflow_dispatch", False),
        (NONE, "", False),  # a local run
        (FAILING, "pull_request", False),
        (NOT_LIVE, "pull_request", False),
        (None, "pull_request", False),  # nothing to wait for: the record holds
    ],
)
def test_only_a_missing_record_on_a_pr_run_is_pending(
    problem: str | None, event: str, pending: bool
) -> None:
    assert record_mod.host_docker_pending(problem, event) is pending


def test_the_event_is_read_from_github_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert record_mod.host_docker_pending(NONE)
    monkeypatch.delenv("GITHUB_EVENT_NAME")
    assert not record_mod.host_docker_pending(NONE)


def _target(monkeypatch: pytest.MonkeyPatch, problem: str | None, event: str | None) -> str:
    """The G-E2 target's outcome with the record reading planted: passed, skipped or failed."""
    monkeypatch.setattr(record_mod, "host_docker_problem", lambda _anchor, cwd=None: problem)
    if event is None:
        monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    else:
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    try:
        g_e2.test_target_host_docker_record_passes_live_compose()
    except pytest.skip.Exception as skipped:
        assert "G-E2 pending" in str(skipped)
        return "skipped"
    except TargetUnmet as unmet:
        assert unmet.gap == "G-E2" and unmet.detail == problem
        return "failed"
    return "passed"


@pytest.mark.parametrize(
    ("problem", "event", "outcome"),
    [
        (NONE, "pull_request", "skipped"),
        (NONE, "push", "failed"),
        (NONE, None, "failed"),
        (FAILING, "pull_request", "failed"),
        (NOT_LIVE, "pull_request", "failed"),
        (None, "pull_request", "passed"),
        (None, "push", "passed"),
    ],
)
def test_the_g_e2_target_reports_pending_only_for_a_missing_record_on_a_pr_run(
    monkeypatch: pytest.MonkeyPatch, problem: str | None, event: str | None, outcome: str
) -> None:
    assert _target(monkeypatch, problem, event) == outcome
