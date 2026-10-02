"""L.SL-4.1: retry through the MC-12 MCP host, one `run(completion="terminal")` call each.

`transient_leaf` (tests/fixtures/workflows/transient_leaf.py) is a published workflow plugin,
written against the public unit-author surface, whose create effect fails on its first call.
A code the declaration lists as retryable is cured by the loop's retry inside the one call (two
tickets, the first NOT_APPLIED, the second APPLIED, the created marker released); a code it does
not list is never retried (one ticket, the node FAILED with that code); a `ONCE` effect whose
outcome is UNKNOWN is never issued again (one ticket, BLOCKED `execution.effect_unconfirmed`).

The lane is read through the proof court's own oracle (`tests.proof.records`); every timing bound
comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from trestle.common import clock

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "workflows"
FIXTURE = "transient_leaf"
RETRYABLE = "marker.transient"
UNDECLARED = "marker.other"
CREATE_EFFECT = "up"
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 60.0


@contextmanager
def _host(tmp_path: Path, *, once: bool = False) -> Iterator[mcp_host.McpHost]:
    """`once` publishes the fixture with its leaf declared `Repeat.ONCE` (one source line)."""
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        source = (FIXTURES / f"{FIXTURE}.py").read_text(encoding="utf-8")
        if once:
            assert source.count("REPEAT = Repeat.SAFE\n") == 1
            source = source.replace("REPEAT = Repeat.SAFE\n", "REPEAT = Repeat.ONCE\n")
        (host.home / "plugins" / f"{FIXTURE}.py").write_text(source, encoding="utf-8")
        yield host


def _terminal(host: mcp_host.McpHost, **args: Any) -> tuple[dict[str, Any], list[Any]]:
    """The one call, and the run's lane rows read from its directory."""
    answer = host.call(
        "run",
        {
            "plugin": FIXTURE,
            "args": {"env": "dev", **args},
            "wait_ms": int(tolerances.HARNESS_WAIT_MS),
            "completion": "terminal",
        },
    )
    assert isinstance(answer, dict), answer
    (run_dir,) = sorted((host.home / "runs").glob(f"*/{answer['run_id']}"))
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return answer, lane.rows


def _of(rows: list[Any], cls: str, effect: str = CREATE_EFFECT) -> list[dict[str, Any]]:
    return [r.entry for r in rows if r.cls == cls and r.entry.get("effect") == effect]


@pytest.mark.proves("WR-IDEM-3", "WR-IDEM-3:once-never-reissued", "A", "single", "LOGIC", "CI")
@pytest.mark.proves("WR-IDEM-3", "A3.2", "A", "single", "LOGIC+MCP", "CI")
def test_transient_cured_by_retry_one_call(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        answer, rows = _terminal(host, mode="cure", fails=1)
    assert answer["answer"]["outcome"] == "passed", answer
    issues = _of(rows, "issue")
    assert [i["attempt"] for i in issues] == [1, 2], "one retry, inside the one call"
    confirmations = _of(rows, "confirmation")
    assert [(c["status"], c.get("code")) for c in confirmations] == [
        ("not_applied", RETRYABLE),
        ("applied", None),
    ]
    assert [r.cls for r in rows].count("end") == 1
    assert len(_of(rows, "released")) == 1, "the created marker is released after the run"


def test_undeclared_code_is_never_retried_one_call(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        answer, rows = _terminal(host, mode="undeclared", fails=1)
    assert answer["answer"]["outcome"] == "failed", answer
    assert answer["answer"]["primary"]["code"] == UNDECLARED, answer
    assert [i["attempt"] for i in _of(rows, "issue")] == [1], (
        "no new claim after an undeclared code"
    )


@pytest.mark.proves("WR-IDEM-3", "WR-IDEM-3:once-never-reissued", "A", "single", "LOGIC", "CI")
def test_once_after_unknown_is_never_reissued_one_call(tmp_path: Path) -> None:
    with _host(tmp_path, once=True) as host:
        answer, rows = _terminal(host, mode="unknown", fails=1)
    primary = answer["answer"]["primary"]
    assert (primary["condition"], primary["code"]) == ("blocked", "execution.effect_unconfirmed")
    assert primary["human_action"], answer  # V-3.7 presence: the operator decides, not the loop
    assert [i["attempt"] for i in _of(rows, "issue")] == [1], "a ONCE effect is issued once"
