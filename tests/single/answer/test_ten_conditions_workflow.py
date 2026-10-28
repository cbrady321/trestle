"""L.SL-9.1: the four workflow-result conditions each land in exactly one class (B4-T2, B4-T3), the
one answer carries what that class requires (B4-C5), and a blocked answer names the human action
and the re-send hint (B4-C5, V-3.7, V-11.1). No class logic is added by this leaf: every case is a
one-vertex workflow on the fakes (`conditions_leaf`), published to the MC-12 MCP host and answered
by the host's one projection (L.SV-4.2) in one `run(completion="terminal")` call.

- assertion failure: a recorded failing result (J-6) -> B4-T2 row 11 -> `failed`.
- MFA needed: the (stub) grant port's refresh is NOT_APPLIED(CREDENTIAL_INTERACTIVE), joined by
  J-5a -> row 9 -> `blocked`.
- service never ready: POSTCONDITION_TIMEOUT -> row 8 -> node class EXHAUSTED, answered `blocked`
  (B4-T3, `EXHAUSTED_AS`; OQ-32 answered: one variant, B4-I2).
- remediation makes no progress: REMEDY_NO_PROGRESS (J-3a) -> row 9 -> `blocked`; never `passed`,
  never `repaired`, never clean (B4-I2, B4-I3).

The expected values are transcribed from B4-T2/B4-T3, V-3.1 and V-11.1, not read back from the
product's tables. The lane is read through the proof court's own oracle (`tests.proof.records`);
every timing bound comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05)."""

from __future__ import annotations

import copy
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from trestle.common import clock

FIXTURE_FILE = Path(__file__).resolve().parent / "fixtures" / "conditions_leaf.py"
PLUGIN = "conditions_leaf"
CONDITIONS = ("assertion_failure", "mfa_needed", "never_ready", "no_progress")
FAILED_CODE = "test.assertion_failed"
SUBJECT = "demo-user@example.test"
COUNTS = {"passed": 3, "failed": 1, "errors": 0, "skipped": 0}
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 60.0
# B4-T2 / B4-T3, transcribed: condition -> (outcome, node class, condition, code, resend)
EXPECTED: dict[str, tuple[str, str, str, str, str | None]] = {
    "assertion_failure": ("failed", "failed", "failed", FAILED_CODE, None),
    "mfa_needed": (
        "blocked",
        "blocked",
        "blocked",
        "execution.credential_interactive",
        "succeeds_after_action",
    ),
    "never_ready": (
        "blocked",
        "exhausted",
        "failed",
        "execution.postcondition_timeout",
        "unknown",
    ),
    "no_progress": (
        "blocked",
        "blocked",
        "blocked",
        "execution.remedy_no_progress",
        "unknown",
    ),
}
BLOCKED = tuple(c for c in CONDITIONS if EXPECTED[c][0] == "blocked")
# V-11.1, transcribed
HUMAN_ACTION: dict[str, str] = {
    "mfa_needed": f"Authenticate identity {SUBJECT} interactively with its issuer, then re-send.",
    "never_ready": "did not become ready within its wait, from its evidence.",
    "no_progress": "the fault its repair targets persisted after the repair.",
}
HUMAN_ACTION_MAX = 512  # V-13 HUMAN_ACTION_MAX (provisional value at V-13)
CLASSES = frozenset({"passed", "failed", "blocked", "cancelled", "timed_out", "execution_error"})


@dataclass(frozen=True)
class Answered:
    """What one call returned and, for cross-checks only, the lane it left."""

    reply: dict[str, Any]
    rows: list[Any]

    @property
    def answer(self) -> dict[str, Any]:
        answer = self.reply["answer"]
        assert isinstance(answer, dict), self.reply
        return answer

    def end(self) -> dict[str, Any]:
        (end,) = [r.entry for r in self.rows if r.cls == "end"]
        assert isinstance(end, dict)
        return end


@contextmanager
def _host(tmp_path: Path, condition: str) -> Iterator[mcp_host.McpHost]:
    """The fixture published with its `CONDITION` source line rewritten (one value per file)."""
    source = FIXTURE_FILE.read_text(encoding="utf-8")
    marker = 'CONDITION = "assertion_failure"\n'
    assert source.count(marker) == 1
    source = source.replace(marker, f'CONDITION = "{condition}"\n')
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        (host.home / "plugins" / f"{PLUGIN}.py").write_text(source, encoding="utf-8")
        yield host


def _one_call(tmp_path: Path, condition: str) -> Answered:
    with _host(tmp_path, condition) as host:
        reply = host.call(
            "run",
            {
                "plugin": PLUGIN,
                "args": {"env": "dev"},
                "wait_ms": int(tolerances.HARNESS_WAIT_MS),
                "completion": "terminal",
            },
        )
        assert isinstance(reply, dict) and "answer" in reply, reply
        (run_dir,) = sorted((host.home / "runs").glob(f"*/{reply['run_id']}"))
        lane = records.lane_rows(run_dir)
        assert not lane.problems and not lane.torn, lane.problems
        shutil.rmtree(run_dir / "tmp", ignore_errors=True)
        return Answered(reply, lane.rows)


@pytest.fixture(scope="module")
def answered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Answered]:
    """The one answer of each condition, produced once for the module (one call each)."""
    return {c: _one_call(tmp_path_factory.mktemp(c), c) for c in CONDITIONS}


@pytest.mark.proves("A1.3", "A1.3:single", "A", "single", "MCP+PROC+STUB", "CI")
@pytest.mark.parametrize("condition", CONDITIONS)
def test_condition_in_exactly_one_class(condition: str, answered: dict[str, Answered]) -> None:
    outcome, node_class, node_condition, code, _ = EXPECTED[condition]
    answer = answered[condition].answer
    # exactly one class, from the closed set: one string, never absent, never a pair
    assert isinstance(answer["outcome"], str) and answer["outcome"] in CLASSES
    assert answer["outcome"] == outcome, (condition, answer)
    primary = answer["primary"]
    assert (primary["node_class"], primary["condition"], primary["code"]) == (
        node_class,
        node_condition,
        code,
    ), (condition, primary)
    assert primary["path"] == [] and primary["listing"] in {"candidate", "rolled_up"}
    # the class the answer names is the only one the conditions can hold: every other class'
    # own fields stay empty (`error` for execution_error, `incomplete` for timed_out)
    assert answer["error"] is None and answer["incomplete"] is None
    assert answer["root_stop"] is None and answer["recovered"] is False


def test_required_fields_decidable_from_answer_alone(answered: dict[str, Answered]) -> None:
    for condition in CONDITIONS:
        # the answer only: no lane, no run view, no second call is read past this point
        answer = copy.deepcopy(answered[condition].answer)
        primary = answer["primary"]
        assert set(answer["cleanup"]) == {
            "clean",
            "released",
            "nothing_created",
            "unknown",
            "left_durable",
            "group_confirmed_gone",
            "helpers_disclosed",
            "lease_ended_unconfirmed",
        }, condition
        if answer["outcome"] == "failed":  # B4-C5: code, path, test_counts, the detail handle
            assert primary["code"] == FAILED_CODE
            assert primary["path"] == []
            assert answer["test_counts"] == COUNTS
            assert isinstance(answer["detail"], str) and answer["detail"].endswith("/answer")
        else:  # blocked: code, human action, re-send (V-3.7's presence rule)
            assert answer["outcome"] == "blocked", condition
            assert isinstance(primary["code"], str) and primary["code"], condition
            assert isinstance(primary["human_action"], str) and primary["human_action"], condition
            assert primary["resend"] in {"succeeds_after_action", "unknown"}, condition
            assert answer["test_counts"] is None, condition
        assert answer["listed_count"] == 0 and answer["unconfirmed_count"] == 0, condition


@pytest.mark.proves("WR-TERM-4", "WR-TERM-4:blocked-names-action", "A", "single", "LOGIC", "CI")
@pytest.mark.parametrize("condition", BLOCKED)
def test_blocked_names_human_action_and_resend_hint(
    condition: str, answered: dict[str, Answered]
) -> None:
    got = answered[condition]
    primary = got.answer["primary"]
    text, resend = primary["human_action"], primary["resend"]
    assert isinstance(text, str) and 0 < len(text.encode("utf-8")) <= HUMAN_ACTION_MAX
    expected = HUMAN_ACTION[condition]
    if condition == "mfa_needed":
        assert text == expected  # V-11.1 with {subject} filled from Confirmation.identity
    else:
        assert text.startswith("Diagnose ") and text.endswith(expected), text
    assert resend == EXPECTED[condition][4]
    # byte-identical to the NodeEnd the loop wrote (B4-C6), and the same pair the presence rule
    # gives every blocked node
    end = got.end()
    assert (end["human_action"], end["resend"]) == (text, resend)
    assert (end["condition"], end["code"]) == (EXPECTED[condition][2], primary["code"])


def test_failed_names_no_human_action(answered: dict[str, Answered]) -> None:
    """A `failed` answer is the one that names a detail, not an action: no human action or resend
    is invented for a test that failed (V-3.7's presence rule does not cover it)."""
    primary = answered["assertion_failure"].answer["primary"]
    assert primary["human_action"] is None and primary["resend"] is None
    assert answered["assertion_failure"].end()["condition"] == "failed"


def test_mfa_blocked_through_j5a_credential_interactive(answered: dict[str, Answered]) -> None:
    got = answered["mfa_needed"]
    rows = got.rows
    (issue,) = [r.entry for r in rows if r.cls == "issue"]
    (confirmation,) = [r.entry for r in rows if r.cls == "confirmation"]
    assert issue["effect"] == "refresh" and issue["facet"] == "safe_start"
    assert confirmation["status"] == "not_applied"
    assert confirmation["code"] == "execution.credential_interactive"
    assert confirmation["identity"] == SUBJECT
    # J-5a: BLOCKED with the port's code, the subject from Confirmation.identity, no retry, no
    # second ticket
    end = got.end()
    assert (end["condition"], end["code"]) == ("blocked", "execution.credential_interactive")
    assert SUBJECT in end["human_action"]
    assert [r.cls for r in rows].count("issue") == 1
    primary = got.answer["primary"]
    assert (primary["code"], primary["resend"]) == (
        "execution.credential_interactive",
        "succeeds_after_action",
    )
    assert primary["human_action"] == HUMAN_ACTION["mfa_needed"]


def test_never_ready_exhausted_answered_blocked(answered: dict[str, Answered]) -> None:
    got = answered["never_ready"]
    answer = got.answer
    # the node's class is EXHAUSTED (row 8); the answer's is blocked (B4-T3, EXHAUSTED_AS)
    assert answer["primary"]["node_class"] == "exhausted"
    assert answer["outcome"] == "blocked" and answer["outcome"] != "failed"
    assert answer["primary"]["code"] == "execution.postcondition_timeout"
    # the wait really ran out: polls were made, the claim was made once, no remedy was granted
    issues = [r.entry for r in got.rows if r.cls == "issue"]
    assert [i["effect"] for i in issues if i.get("remedy") is None][0] == "up"
    assert all(i.get("remedy") is None for i in issues)
    end = got.end()
    assert (end["condition"], end["code"]) == ("failed", "execution.postcondition_timeout")
    assert end["human_action"] and end["resend"] == "unknown"  # V-3.7: the presence rule
    assert answer["primary"]["disposition"] is None


@pytest.mark.proves(
    "WR-TERM-3",
    "WR-TERM-3:no-progress-one-class-never-passed",
    "A",
    "single",
    "MCP+PROC+STUB",
    "CI",
)
@pytest.mark.proves(
    "WR-TERM-3", "WR-TERM-3:no-progress-class-blocked", "A", "single", "MCP+PROC+STUB", "CI"
)
def test_no_progress_blocked_never_passed_repaired_or_clean(
    answered: dict[str, Answered],
) -> None:
    got = answered["no_progress"]
    answer = got.answer
    primary = answer["primary"]
    assert (answer["outcome"], primary["node_class"]) == ("blocked", "blocked")
    assert primary["code"] == "execution.remedy_no_progress"
    # the repair was really made and confirmed (a granted remedy on a confirmed ticket) ...
    repairs = [r.entry for r in got.rows if r.cls == "issue" and r.entry.get("remedy") is not None]
    assert len(repairs) == 1 and repairs[0]["effect"] == "restart"
    # ... and the fault persisted, so the answer is never passed and never repaired
    assert answer["outcome"] not in {"passed"}
    assert primary["node_class"] not in {"passed", "repaired"}
    assert primary["disposition"] is None  # dispositions are for passed and repaired nodes only
    # `cleanup.clean` is the cleanup's own completeness (B4-C7), not the node's: the marker the
    # repair could not fix was still stopped and released, so it says so and never stands in for
    # the class (B4-I3: a repair is never reported clean, i.e. never the `passed` class)
    assert answer["cleanup"]["unknown"] == 0 and answer["cleanup"]["released"] >= 1
    end = got.end()
    assert (end["condition"], end["code"]) == ("blocked", "execution.remedy_no_progress")
