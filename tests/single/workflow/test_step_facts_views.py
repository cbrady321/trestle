"""L.SL-10.1: step facts as structured fields through the existing views (WR-EVID-3
`step-facts-structured`; B2-C13, V-13 `EvidenceSink`; BFD-32, BFD-34).

The loop reports each step as an event of a reserved kind through `RunServices.evidence`:
`plan.identity`, `step.observed`, `step.postcondition`, `step.action`, `step.repair` and
`step.cleanup`, each with a structured payload, so `run_events` (unchanged: `{run_id, event_seq,
kind, payload}`) answers what the run did without a line of prose. Two levels: the real host runs
the published fixture `tests/fixtures/workflows/remedy_leaf.py` (a real child process, the real
`query` tool, the lane read through the proof court's own oracle), and the loop tests' rig shows
the fields the host run cannot easily provoke (a test's recorded result, a unit that tries to forge
a fact)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from tests.single.workflow import loopkit as kit
from tests.single.workflow.loopkit import Runner, Unit, declaration, effect, observation
from trestle.child.context import RESERVED_EVENT_KINDS, RuntimeContext
from trestle.child.context import STEP_FACT_KINDS as CONTEXT_STEP_FACT_KINDS
from trestle.common import clock
from trestle.workflow import loop
from trestle.workflow.declarations import CompletionSource, EffectFacetClass
from trestle.workflow.units import Acted
from trestle.workflow.values import (
    CheckResult,
    Confirmation,
    ConfirmationStatus,
    RecordedResult,
    TestCounts,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "workflows"
FIXTURE = "remedy_leaf"
HOT = "marker.hot"
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 60.0
KINDS = (
    "plan.identity",
    "step.observed",
    "step.postcondition",
    "step.action",
    "step.repair",
    "step.cleanup",
)


# ------------------------------------------------------------------------------ the real host


@contextmanager
def _host(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        source = (FIXTURES / f"{FIXTURE}.py").read_text(encoding="utf-8")
        (host.home / "plugins" / f"{FIXTURE}.py").write_text(source, encoding="utf-8")
        yield host


def _events(host: mcp_host.McpHost, run_id: str) -> list[dict[str, Any]]:
    """Every `run_events` row of a run, following `next_cursor` until it ends."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(50):
        args: dict[str, Any] = {"view": "run_events", "params": {"run_id": run_id}}
        if cursor is not None:
            args["cursor"] = cursor
        page = host.call("query", args)
        assert "items" in page, page
        rows.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows
    raise AssertionError("run_events did not end within 50 pages")


@pytest.fixture(scope="module")
def repaired_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One real run of `remedy_leaf`: it creates its marker, finds it unhealthy, repairs it once
    (a remedy ticket), passes, and releases the marker."""
    tmp_path = tmp_path_factory.mktemp("step-facts")
    with _host(tmp_path) as host:
        answer = host.call(
            "run",
            {
                "plugin": FIXTURE,
                "args": {"env": "dev", "lag": 1},
                "wait_ms": int(tolerances.HARNESS_WAIT_MS),
                "completion": "terminal",
            },
        )
        assert isinstance(answer, dict), answer
        run_id = answer["run_id"]
        rows = _events(host, run_id)
        (run_dir,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return {"answer": answer, "rows": rows, "lane": lane.rows}


def _of(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r["payload"] for r in rows if r["kind"] == kind]


def _lane(rows: list[Any], cls: str) -> list[dict[str, Any]]:
    return [r.entry for r in rows if r.cls == cls]


@pytest.mark.proves("WR-EVID-3", "WR-EVID-3:step-facts-structured", "A", "single", "PROC", "CI")
def test_every_fact_queryable_as_structured_field(repaired_run: dict[str, Any]) -> None:
    """Plan identity, observations, actions, repairs, postconditions and cleanup are each rows of
    `run_events` with their facts as payload fields; none is only prose, and the row shape is the
    old one."""
    answer, rows, lane = repaired_run["answer"], repaired_run["rows"], repaired_run["lane"]
    assert answer["answer"]["outcome"] == "passed", answer
    assert all(set(r) == {"run_id", "event_seq", "kind", "payload"} for r in rows)
    assert [r["event_seq"] for r in rows] == sorted(r["event_seq"] for r in rows)
    for kind in KINDS:
        assert _of(rows, kind), f"no {kind} in run_events"
        assert all(isinstance(p, dict) and "message" not in p for p in _of(rows, kind)), kind

    (identity,) = _of(rows, "plan.identity")
    (plan_row,) = _lane(lane, "plan")
    assert identity["declaration_digest"] == plan_row["declaration_digest"]
    assert identity["args_hash"] == plan_row["args_hash"]
    assert identity["plan_digest"] and identity["selection"] == []

    observed = _of(rows, "step.observed")
    assert observed[0]["path"] == "(root)" and observed[0]["selector_present"] is False
    assert any(o["selector_present"] is True for o in observed)
    assert {"present", "identity_proven", "configuration_compatible", "code", "found"} <= set(
        observed[0]
    )

    (first, *_), *_more = (a["tickets"] for a in _of(rows, "step.action") if a["tickets"])
    assert (first["effect"], first["attempt"], first["status"]) == ("up", 1, "applied")
    assert first["facet"] == "create" and first["remedy"] is None
    assert _of(rows, "step.action")[0]["returned"] == "Acted"

    (repair,) = _of(rows, "step.repair")
    assert (repair["effect"], repair["status"]) == ("restart", "applied")
    assert repair["remedy"] == {"code": HOT, "attempt": 1}
    remedied = [a["remedy"] for a in _of(rows, "step.action") if a["remedy"] is not None]
    assert remedied == [{"code": HOT, "effect": "restart", "attempt": 1}]

    checks = _of(rows, "step.postcondition")
    unready = [c for c in checks if c["postcondition"] and not c["postcondition"]["satisfied"]]
    assert any(c["postcondition"]["code"] == HOT for c in unready)
    assert (checks[-1]["condition"], checks[-1]["code"]) == ("satisfied", None)
    assert isinstance(checks[-1]["attempts"], int)

    (cleanup,) = _of(rows, "step.cleanup")
    assert cleanup["effect"] == "up" and cleanup["released"] is True and cleanup["selector"]
    assert [t["effect"] for t in cleanup["tickets"]] == ["stop"]


def test_reconstruct_run_from_evidence_alone(repaired_run: dict[str, Any]) -> None:
    """The run is rebuilt from the `run_events` rows alone (never the lane) and matches the lane:
    the same tickets in the same order with the same answers, the same repair, the same released
    handle and the same end."""
    rows, lane = repaired_run["rows"], repaired_run["lane"]
    facts = [r for r in rows if r["kind"] in KINDS]
    tickets: list[dict[str, Any]] = []
    for row in facts:
        if row["kind"] in ("step.action", "step.cleanup"):
            tickets.extend(row["payload"]["tickets"])
    rebuilt = [(t["effect"], t["attempt"], t["status"], t["code"]) for t in tickets]
    issued = _lane(lane, "issue")
    confirmed = {(c["effect"], c["attempt"]): c for c in _lane(lane, "confirmation")}
    from_lane = [
        (
            i["effect"],
            i["attempt"],
            confirmed[(i["effect"], i["attempt"])]["status"],
            confirmed[(i["effect"], i["attempt"])].get("code"),
        )
        for i in issued
    ]
    assert rebuilt == from_lane, "the tickets, in order, with their answers"
    assert [t["remedy"] for t in tickets if t["remedy"] is not None] == [
        {"code": i["remedy"]["code"], "attempt": i["remedy"]["attempt"]}
        for i in issued
        if i["remedy"] is not None
    ]
    released = [t for t in facts if t["kind"] == "step.cleanup" and t["payload"]["released"]]
    assert [r["payload"]["effect"] for r in released] == [
        e["effect"] for e in _lane(lane, "released")
    ]
    (end,) = _lane(lane, "end")
    last = [r["payload"] for r in facts if r["kind"] == "step.postcondition"][-1]
    assert (last["condition"], last["code"], last["provenance"]) == (
        end["condition"],
        end["code"],
        end["provenance"],
    )
    # the order of the story: identity first, cleanup after the last verdict
    kinds = [r["kind"] for r in facts]
    assert kinds[0] == "plan.identity" and kinds[-1] == "step.cleanup"
    assert kinds.index("step.action") < kinds.index("step.repair")


# ------------------------------------------------------------------------------ the loop rig


def _rig(tmp_path: Path, unit: Unit, ports: dict[type, object]) -> kit.Rig:
    rig = kit.build(tmp_path, unit, ports=ports)
    return rig


def _facts(rig: kit.Rig, kind: str) -> list[dict[str, Any]]:
    return [dict(fields) for k, fields in rig.sink.events if k == kind]


def test_a_tests_recorded_result_is_a_field_of_its_action(tmp_path: Path) -> None:
    """An event effect's recorded result (a test's pass or fail and its counts, V-5.5) is a field
    of the `step.action` fact, not prose."""
    decl = declaration(
        completion=CompletionSource.RECORDED,
        effects=(effect(kit.RUN_EFFECT, EffectFacetClass.EVENT, release_timeout_s=None),),
    )

    class Recorded:
        recorded = RecordedResult(False, "unit.test_failed", TestCounts(7, 2, 1, 3))

    class Port(Runner):
        def run(self, name: str, ticket: Any) -> Any:
            self.calls += 1
            return Confirmation(ConfirmationStatus.APPLIED, None, None), Recorded()

    def observe(unit: Unit, params: Any, reads: Any, ctx: Any) -> Any:
        return observation()

    def advance(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        effects.event(kit.RunPort).run("t", kit.RUN_EFFECT)
        return Acted()

    rig = _rig(tmp_path, Unit(decl, observe, advance), {kit.RunPort: Port()})
    rig.run()
    (action,) = _facts(rig, "step.action")
    (ticket,) = action["tickets"]
    assert ticket["facet"] == "event" and ticket["status"] == "applied"
    assert ticket["result"] == {
        "passed": False,
        "code": "unit.test_failed",
        "counts": {"passed": 7, "failed": 2, "errors": 1, "skipped": 3},
    }
    (verdict,) = _facts(rig, "step.postcondition")[-1:]
    assert (verdict["condition"], verdict["code"]) == ("failed", "unit.test_failed")


def test_a_unit_cannot_write_a_step_fact(tmp_path: Path) -> None:
    """The step-fact kinds are the loop's alone: the evidence a unit gets in its context drops
    them, and passes every other kind through."""
    decl = declaration(effects=kit.MARKER_EFFECTS)
    marker = kit.Marker()
    seen: list[Any] = []

    def observe(unit: Unit, params: Any, reads: Any, ctx: Any) -> Any:
        for kind in KINDS:
            ctx.evidence.event(kind, {"forged": True})
        ctx.evidence.event("unit_note", {"n": 1})
        seen.append(ctx.evidence)
        return observation(selector_present=True, ready=True)

    unit = kit.marker_unit(marker, decl)
    rig = _rig(
        tmp_path, Unit(decl, observe, unit._advance, unit._release), kit.marker_ports(marker)
    )
    rig.run()
    assert seen and not any("forged" in fields for _, fields in rig.sink.events)
    assert _facts(rig, "unit_note"), "an event of any other kind reaches the sink"
    # the loop's own facts are there (this unit's resource was found ready: no action, no cleanup)
    assert all(
        _facts(rig, kind) for kind in ("plan.identity", "step.observed", "step.postcondition")
    )


def test_a_fact_the_sink_cannot_take_never_fails_the_node(tmp_path: Path) -> None:
    """B2-C13: the sink never raises into the loop, and the loop ignores what it does with a
    fact; a sink whose every call fails silently still lets the node pass."""
    decl = declaration(effects=kit.MARKER_EFFECTS)
    marker = kit.Marker()
    unit = kit.marker_unit(marker, decl)
    rig = _rig(tmp_path, unit, kit.marker_ports(marker))
    rig.sink.event = lambda kind, fields: None  # type: ignore[method-assign]
    rig.run()
    assert rig.ends()[0]["condition"] in ("satisfied", "failed")


# ------------------------------------------------------------------------------ the context


def _context(tmp_path: Path) -> RuntimeContext:
    evidence = tmp_path / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        work=tmp_path / "work",
        evidence=evidence,
        deadline=datetime.now(UTC) + timedelta(seconds=60),
        events_path=evidence / "events.ndjson",
    )


def _written(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "evidence" / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("kind", KINDS)
def test_step_fact_kinds_are_reserved_from_plugins_and_open_to_the_runtime(
    tmp_path: Path, kind: str
) -> None:
    """`Context.event` refuses each step-fact kind (a plugin cannot forge one); the runtime's own
    `runtime_event` writes it through the same `_emit`, and still refuses every other reserved
    kind (the lane classes)."""
    ctx = _context(tmp_path)
    with pytest.raises(ValueError):
        ctx.event(kind, forged=True)
    assert _written(tmp_path) == []
    ctx.runtime_event(kind, path="(root)", n=1)
    assert _written(tmp_path)[0]["kind"] == kind
    assert _written(tmp_path)[0]["payload"] == {"path": "(root)", "n": 1}
    for lane_kind in ("issue", "confirmation", "plan", "step", "node_end"):
        with pytest.raises(ValueError):
            ctx.runtime_event(lane_kind, forged=True)


def test_the_two_step_fact_sets_are_one() -> None:
    """The loop's kinds and the context's reserved kinds are the same set, and all are reserved."""
    assert loop.STEP_FACT_KINDS == CONTEXT_STEP_FACT_KINDS == frozenset(KINDS)
    assert CONTEXT_STEP_FACT_KINDS <= RESERVED_EVENT_KINDS


def test_an_unready_check_is_reported_as_fields(tmp_path: Path) -> None:
    """A check result is a pair of fields (`satisfied`, `code`), never folded into text."""
    decl = declaration(effects=kit.MARKER_EFFECTS)
    marker = kit.Marker(ready_after=10_000)
    unit = kit.marker_unit(marker, decl)
    rig = _rig(tmp_path, unit, kit.marker_ports(marker))
    rig.run()
    checks = _facts(rig, "step.postcondition")
    assert checks
    assert isinstance(checks[-1]["postcondition"], dict)
    assert CheckResult(False, None, "").satisfied is False
    assert all(
        isinstance(c["postcondition"]["satisfied"], bool) for c in checks if c["postcondition"]
    )
