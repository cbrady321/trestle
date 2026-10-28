"""L.SL-6.2: retry and remediation through the MC-12 MCP host, one `run(completion="terminal")`
call each (WR-TERM-1, WR-TERM-4, WR-REMEDY-5).

`remedy_leaf` (tests/fixtures/workflows/remedy_leaf.py) is a published workflow plugin, written
against the public unit-author surface, whose one leaf creates a marker that stays unhealthy until
it is restarted, and declares that restart as a remedy for the trigger code the marker reports.
The variants below are that source with declared fields substituted, never another plugin:

- one leaf, one retry (the create is refused once with a code the leaf declares retryable), one
  remediation (the restart the loop grants when the wait runs out), and a wait long enough that
  the run takes about three times `run`'s default wait: one tool request, a terminal answer;
- the answer is class `passed` with the resource disposition `repaired`, never `started` (clean);
- a remedy that cannot cure the marker is exhausted or makes no progress: the run ends BLOCKED
  with that stable code, never a generic failure.

The lane is read through the proof court's own oracle (`tests.proof.records`); every timing bound
comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05)."""

from __future__ import annotations

import inspect
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from trestle.common import clock
from trestle.server.control import ControlSurface

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "workflows"
FIXTURE = "remedy_leaf"
CLEAN = "transient_leaf"  # never repairs: its create works when it fails 0 times
CREATE_EFFECT = "up"
RESTART_EFFECT = "restart"
BUSY = "marker.busy"
HOT = "marker.hot"
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 120.0

# `run`'s own default bounded wait (the parameter's default, read, not restated)
DEFAULT_WAIT_S = int(inspect.signature(ControlSurface.run).parameters["wait_ms"].default) / 1000
LONG_RUN_FACTOR = 3  # "about 3x the default bounded wait" (WR-TERM-1)

_ENTRY = "ENTRY = WorkflowEntry(\n    root=UNIT, units={UNIT: RemedyLeaf(declaration())}"
_MARKER_RESTART = "    def restart(self, target: Any, ticket: Any) -> Confirmation:\n"
_FLAKY_CREATE = """    def create(self, spec: Any, ticket: Any) -> Confirmation:
        self.creates = getattr(self, "creates", 0) + 1
        if self.creates == 1:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, RETRYABLE, None)
        return super().create(spec, ticket)

"""


def _source(*, max_wait_s: float, remedy_attempts: int, flaky_create: bool) -> str:
    """The fixture with its declared wait (and remedy attempts) and, optionally, a create that is
    refused once with the retryable code: every change is one asserted substitution."""
    text = (FIXTURES / f"{FIXTURE}.py").read_text(encoding="utf-8")
    declared = (
        f"declaration(max_wait_s={max_wait_s}, remedy_attempts={remedy_attempts}, "
        "retryable=frozenset({RETRYABLE}), max_attempts=6)"
    )
    assert text.count(_ENTRY) == 1, "the remedy fixture's ENTRY moved"
    text = text.replace(_ENTRY, _ENTRY.replace("declaration()", declared))
    if flaky_create:
        assert text.count(_MARKER_RESTART) == 1, "the remedy fixture's marker moved"
        text = text.replace(_MARKER_RESTART, _FLAKY_CREATE + _MARKER_RESTART)
    return text


@contextmanager
def _host(tmp_path: Path, source: str) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        (host.home / "plugins" / f"{FIXTURE}.py").write_text(source, encoding="utf-8")
        yield host


def _terminal(
    host: mcp_host.McpHost, plugin: str = FIXTURE, **args: Any
) -> tuple[dict[str, Any], list[Any], float]:
    """The one call, the run's lane rows read from its directory, and how long the call took."""
    started = time.monotonic()
    answer = host.call(
        "run",
        {
            "plugin": plugin,
            "args": {"env": "dev", **args},
            "wait_ms": int(tolerances.HARNESS_WAIT_MS),
            "completion": "terminal",
        },
    )
    elapsed = time.monotonic() - started
    assert isinstance(answer, dict), answer
    (run_dir,) = sorted((host.home / "runs").glob(f"*/{answer['run_id']}"))
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return answer, lane.rows, elapsed


def _of(rows: list[Any], cls: str, effect: str) -> list[dict[str, Any]]:
    return [r.entry for r in rows if r.cls == cls and r.entry.get("effect") == effect]


@pytest.mark.proves("WR-TERM-1", "WR-TERM-1:one-leaf-one-call", "A", "single", "MCP", "CI")
def test_one_leaf_one_retry_one_remediation_one_call(tmp_path: Path) -> None:
    wait_s = DEFAULT_WAIT_S * LONG_RUN_FACTOR
    with _host(tmp_path, _source(max_wait_s=wait_s, remedy_attempts=2, flaky_create=True)) as host:
        requests = host.request_count()
        answer, rows, elapsed = _terminal(host, lag=1)
        assert host.request_count() == requests + 1, "one tool request, no other call"
    assert answer["state"] != "running" and answer["answer"]["outcome"] == "passed", answer
    assert elapsed >= wait_s, "a long run: the one call waited past the default bounded wait"
    creates = _of(rows, "issue", CREATE_EFFECT)
    assert [c["attempt"] for c in creates] == [1, 2], "exactly one retry"
    assert [(c["status"], c.get("code")) for c in _of(rows, "confirmation", CREATE_EFFECT)] == [
        ("not_applied", BUSY),
        ("applied", None),
    ]
    remedies = [i for i in _of(rows, "issue", RESTART_EFFECT) if i["remedy"] is not None]
    assert len(remedies) == 1 and remedies[0]["remedy"]["code"] == HOT, "exactly one remediation"
    assert [r.cls for r in rows].count("end") == 1
    assert len(_of(rows, "released", CREATE_EFFECT)) == 1, "the created marker is released"


@pytest.mark.proves("WR-TERM-4", "WR-TERM-4:repaired-not-clean", "A", "single", "LOGIC", "CI")
def test_repaired_reported_repaired_not_clean(tmp_path: Path) -> None:
    """A run that succeeded only after its remedy is answered `passed` with the node class and
    resource disposition `repaired`; the same kind of run with no repair is `passed` and
    `started` (clean), so `repaired` is the remedy's alone (B4-C3)."""
    source = _source(max_wait_s=1.0, remedy_attempts=2, flaky_create=False)
    with _host(tmp_path / "repaired", source) as host:
        repaired, rows, _ = _terminal(host, lag=1)
    assert len([i for i in _of(rows, "issue", RESTART_EFFECT) if i["remedy"] is not None]) == 1
    assert repaired["answer"]["outcome"] == "passed", repaired
    primary = repaired["answer"]["primary"]
    assert (primary["node_class"], primary["disposition"]) == ("repaired", "repaired"), primary
    assert repaired["outcome"]["class"] == "passed", "the legacy class reads passed as well"

    # the clean twin: a published plugin whose create works first time, no repair
    with mcp_host.McpHost(home=tmp_path / "clean", timeout_s=HOST_TIMEOUT_S) as host:
        text = (FIXTURES / f"{CLEAN}.py").read_text(encoding="utf-8")
        (host.home / "plugins" / f"{CLEAN}.py").write_text(text, encoding="utf-8")
        clean, _, _ = _terminal(host, CLEAN, mode="cure", fails=0)
    assert clean["answer"]["outcome"] == "passed", clean
    primary = clean["answer"]["primary"]
    assert (primary["node_class"], primary["disposition"]) == ("passed", "started"), primary


@pytest.mark.proves("WR-REMEDY-5", "WR-REMEDY-5:coded-exhaustion", "A", "single", "LOGIC+MCP", "CI")
@pytest.mark.parametrize(
    ("attempts", "code"),
    [(1, "execution.remedy_exhausted"), (2, "execution.remedy_no_progress")],
)
def test_exhausted_remedy_is_coded_not_generic(tmp_path: Path, attempts: int, code: str) -> None:
    """A repair that cannot cure the marker (`fixed`: its code never changes) ends BLOCKED with
    the stable code of what happened, in the one call: exhausted when its only attempt is spent,
    no progress when an attempt was still declared."""
    source = _source(max_wait_s=1.0, remedy_attempts=attempts, flaky_create=False)
    with _host(tmp_path, source) as host:
        answer, rows, _ = _terminal(host, fixed=True)
    primary = answer["answer"]["primary"]
    assert (primary["condition"], primary["code"]) == ("blocked", code), answer
    assert primary["human_action"], "V-3.7: a blocked answer names the human action"
    assert answer["answer"]["outcome"] != "passed"
    issued = [i for i in _of(rows, "issue", RESTART_EFFECT) if i["remedy"] is not None]
    assert len(issued) == 1, "one repair was made, and it never cured the marker"
