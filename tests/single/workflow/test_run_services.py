"""L.SV-5.2: the child's RunServices for a workflow entry (B2-C3..C6, B2-C13, B2-C14; MC-B2-02):
identity, the one clock, slice ends, the cancel signal, the attempt lane over the workflow
package's types, the evidence sink, and the plain plugin path unchanged."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.proof import tolerances
from tests.single.control import support as control_support
from tests.single.record import support as sup
from trestle.child import run_services as rs
from trestle.child.context import RuntimeContext
from trestle.common import clock, codes
from trestle.common import lane_format as lf
from trestle.common.limits import CaptureLimits
from trestle.common.plan import bounds, carving
from trestle.common.plan.compiler import AdmittedPlan, Vertex, implicit_depth1_plan
from trestle.common.types import AdmitRequest, AdmitResultAdmitted
from trestle.workflow import services as svc
from trestle.workflow import units, values
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat

REPO = Path(__file__).resolve().parents[3]
DEADLINE = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 30, 11, 55, 0, tzinfo=UTC)
DECL = "d" * 64
RELEASE_SLICE_S = 10.0
RESERVE_S = 10.0
DEADLINE_S = 300.0


def np(*segments: str) -> values.NodePath:
    return values.NodePath(tuple(segments))


def tree_plan() -> AdmittedPlan:
    """root (all, no budget) over a (20 s) and b (30 s, needs a): a real carve, digest attached."""
    base = implicit_depth1_plan("root")
    vertices = (
        Vertex("", "root", "all", None, ("a", "b"), (), 2, (), "host"),
        Vertex("a", "a", "leaf", 20.0, (), (), 1, (), "host"),
        Vertex("b", "b", "leaf", 30.0, (), ("a",), 1, (), "host"),
    )
    plan = replace(
        base,
        declaration_digest=DECL,
        vertices=vertices,
        edges=(("a", "b"),),
        release_rank={"": 0, "a": 0, "b": 0},
        precedence_ordinal={"": 0, "a": 1, "b": 2},
        lane_entries=bounds.LANE_BASE_ENTRIES + 3 + 2,
    )
    slices = carving.carve(plan, DEADLINE_S, RESERVE_S, RELEASE_SLICE_S)
    assert not isinstance(slices, tuple) and isinstance(slices, dict)
    return carving.attach(plan, slices, RELEASE_SLICE_S)


def make_services(
    tmp_path: Path,
    plan: AdmittedPlan | None = None,
    *,
    events: list[tuple[str, dict[str, Any]]] | None = None,
    event_max: int = 64 * 1024,
    run_id: str = "r_svc_0001",
) -> rs.ChildRunServices:
    run_dir = tmp_path / run_id
    (run_dir / "work").mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
    sink = events if events is not None else []

    def emit(kind: str, **fields: Any) -> None:
        sink.append((kind, fields))

    return rs.build_run_services(
        rs.ServicesInput(
            run_dir=run_dir,
            plan=plan or tree_plan(),
            deadline=DEADLINE,
            event=emit,
            event_max=event_max,
            now=lambda: NOW,
            reserve_s=RESERVE_S,
        )
    )


def _touch(services: rs.ChildRunServices, name: str) -> None:
    (services._given.run_dir / "work" / name).write_text("1", encoding="utf-8")


# --------------------------------------------------------------------------- the protocols


def test_child_services_satisfy_workflow_protocols(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    assert isinstance(services, svc.RunServices)
    assert isinstance(services.attempts(), svc.AttemptLane)
    assert services.attempts() is services.attempts()  # one lane per root process
    # no host section is declared at one vertex (B2-C8 carried): sections() is absent
    assert not hasattr(services, "sections")
    admitted = services.admitted()
    assert admitted.lineage_root == "r_svc_0001"
    assert admitted.accepted.plan_digest == tree_plan().plan_digest
    assert admitted.accepted.lane_entries == bounds.LANE_BASE_ENTRIES + 5

    # B2-C14: a workflow run's Context also satisfies RunContext; a plain plugin's does not
    ctx = _context(tmp_path)
    assert not hasattr(ctx, "run_services")
    with pytest.raises(AttributeError):
        ctx.run_services  # noqa: B018
    ctx.bind_run_services(services)
    assert isinstance(ctx, svc.RunContext) and ctx.run_services is services


def _context(tmp_path: Path, limits: CaptureLimits | None = None) -> RuntimeContext:
    evidence = tmp_path / "ctx-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        work=tmp_path / "ctx-work",
        evidence=evidence,
        deadline=DEADLINE,
        events_path=evidence / "events.ndjson",
        limits=limits,
    )


# --------------------------------------------------------------------------- the one clock


def test_clock_reading_is_data_release_point_is_deadline_minus_release_slice(
    tmp_path: Path,
) -> None:
    plan = tree_plan()
    reading = make_services(tmp_path, plan).clock()
    assert isinstance(reading, values.ClockReading)
    assert [f.name for f in dataclasses.fields(reading)] == [
        "now",
        "root_deadline",
        "release_point",
    ]
    assert not [
        n
        for n in dir(values.ClockReading)
        if not n.startswith("_") and callable(getattr(values.ClockReading, n, None))
    ]  # data only: no method, no slice_end
    assert reading.now == NOW and reading.root_deadline == DEADLINE
    assert reading.release_point == DEADLINE - timedelta(seconds=plan.release_slice)
    assert plan.release_slice == RELEASE_SLICE_S
    # the implicit depth-1 plan has no release slice: its release point is its deadline
    implicit = make_services(tmp_path, implicit_depth1_plan("p"), run_id="r_svc_0002").clock()
    assert implicit.release_point == implicit.root_deadline


def test_slice_end_root_is_release_point(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    assert services.slice_end(np()) == services.clock().release_point
    plan = implicit_depth1_plan("p")
    solo = make_services(tmp_path, plan, run_id="r_svc_0003")
    assert solo.slice_end(np()) == solo.clock().release_point == DEADLINE


def test_slice_end_of_a_child_is_its_carved_end(tmp_path: Path) -> None:
    plan = tree_plan()
    services = make_services(tmp_path, plan)
    carved = carving.carve(plan, DEADLINE_S, RESERVE_S, RELEASE_SLICE_S)
    assert isinstance(carved, dict)
    for path in ("a", "b"):
        expected = DEADLINE - timedelta(seconds=DEADLINE_S - carved[path].end_s)
        assert services.slice_end(np(path)) == expected
    # b needs a: a ends before b's whole budget can run after it
    assert services.slice_end(np("a")) < services.slice_end(np("b")) < services.slice_end(np())


def test_slice_ends_agree_with_carve_for_any_deadline() -> None:
    plan = tree_plan()
    ends = carving.slice_ends(plan, RESERVE_S)
    assert ends[""] == -RELEASE_SLICE_S
    for deadline_s in (200.0, DEADLINE_S, 3600.0):
        carved = carving.carve(plan, deadline_s, RESERVE_S, RELEASE_SLICE_S)
        assert isinstance(carved, dict)
        for path, piece in carved.items():
            assert piece.end_s == pytest.approx(deadline_s + ends[path])
    with pytest.raises(ValueError):
        carving.slice_ends(plan, -1.0)


# --------------------------------------------------------------------------- cancellation


def test_cancel_signal_cause_cancel_before_release_point(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    signal = services.cancellation()
    assert signal.requested is False and signal.cause() is None
    _touch(services, rs.RELEASE_POINT_FLAG)
    assert signal.requested is True and signal.cause() == values.StopCause.RELEASE_POINT
    _touch(services, rs.CANCEL_FLAG)  # both exist: CANCEL names first
    assert signal.requested is True and signal.cause() == values.StopCause.CANCEL

    only_cancel = make_services(tmp_path, run_id="r_svc_0004")
    _touch(only_cancel, rs.CANCEL_FLAG)
    assert only_cancel.cancellation().cause() == values.StopCause.CANCEL


@pytest.mark.parametrize("flag", [rs.CANCEL_FLAG, rs.RELEASE_POINT_FLAG])
def test_cancellation_wait_returns_promptly_on_either_flag(tmp_path: Path, flag: str) -> None:
    services = make_services(tmp_path)
    signal = services.cancellation()
    # Prompt means "on the flag, long before the wait's own timeout": the bound tolerates a
    # scheduler stall on a loaded runner and stays far below JOIN_WAIT_S, so a wait that ignored
    # the flag still fails it (and the True return already proves the flag ended the wait).
    bound = clock.poll_interval + tolerances.SETTLE_LONG_S
    # a flag written while the wait is in progress
    timer = threading.Timer(tolerances.POLL_S, _touch, args=(services, flag))
    timer.start()
    started = time.monotonic()
    assert signal.wait(timedelta(seconds=tolerances.JOIN_WAIT_S)) is True
    assert time.monotonic() - started < tolerances.POLL_S + bound
    timer.join()
    # a flag that already exists: no wait at all
    started = time.monotonic()
    assert signal.wait(timedelta(seconds=tolerances.JOIN_WAIT_S)) is True
    assert time.monotonic() - started < bound


def test_cancellation_wait_times_out_false_without_a_flag(tmp_path: Path) -> None:
    signal = make_services(tmp_path).cancellation()
    started = time.monotonic()
    assert signal.wait(timedelta(seconds=tolerances.POLL_FINE_S)) is False
    # returns at its own short timeout, not hanging (a stall-tolerant bound, as above)
    assert time.monotonic() - started < tolerances.POLL_FINE_S + clock.poll_interval + (
        tolerances.SETTLE_LONG_S
    )
    assert signal.wait(timedelta(0)) is False


# --------------------------------------------------------------------------- identity


def test_lineage_unknown_node_raises(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    assert services.lineage(np()) == values.Lineage("r_svc_0001", np())
    assert services.lineage(np("a")) == values.Lineage("r_svc_0001", np("a"))
    for unknown in (np("nope"), np("a", "x"), np("a/b")):
        with pytest.raises(svc.UnknownNode):
            services.lineage(unknown)
        with pytest.raises(svc.UnknownNode):
            services.child_run_id(unknown)
        with pytest.raises(svc.UnknownNode):
            services.slice_end(unknown)


def test_child_run_id_is_derived_fixed_and_distinct(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    ids = {p: services.child_run_id(np(*p)) for p in ((), ("a",), ("b",))}
    assert len(set(ids.values())) == 3
    assert ids == {p: services.child_run_id(np(*p)) for p in ((), ("a",), ("b",))}
    assert ids[("a",)] == rs.child_run_id("r_svc_0001", np("a"))
    other = make_services(tmp_path, run_id="r_svc_0009")
    assert other.child_run_id(np("a")) != ids[("a",)]
    assert all(i.startswith("r_svc_0001.") and i != "r_svc_0001" for i in ids.values())


# --------------------------------------------------------------------------- evidence


def test_evidence_sink_truncates_never_raises(tmp_path: Path) -> None:
    limits = CaptureLimits(max_single_event_bytes=512)
    ctx = _context(tmp_path, limits)
    sink = rs.ContextEvidenceSink(ctx.event, limits.max_single_event_bytes)

    sink.event("probe", {"n": 1})
    sink.event("probe", {"blob": "x" * 5000, "n": 2})  # over EVENT_MAX: truncated and marked
    sink.event("probe", {"kind": "collides", "n": 3})
    sink.event("probe", {"obj": object()})  # not JSON: stringified, not raised
    sink.event("log", {"n": 4})  # a reserved kind: refused by the context, swallowed here

    # nothing so far was dropped by the context's own limit: the sink kept every event under it
    assert not ctx.limits_markers()
    sink.event("x" * 1000, {"n": 5})  # a kind that alone cannot fit: never raised; the context
    #                                   drops it and marks the drop
    assert [m["limit"] for m in ctx.limits_markers()] == ["max_single_event_bytes"]

    rows = [
        json.loads(line)
        for line in (tmp_path / "ctx-evidence" / "events.ndjson").read_text("utf-8").splitlines()
    ]
    probes = [r for r in rows if r["kind"] == "probe"]
    assert probes[0]["payload"] == {"n": 1}
    marked = probes[1]["payload"]
    assert marked["truncated"] is True and marked["original_bytes"] > 5000
    assert marked["excerpt"].startswith('{"blob":"xxx')
    assert len(json.dumps({"kind": "probe", "payload": marked}, separators=(",", ":"))) <= 512
    assert probes[2]["payload"] == {"field_kind": "collides", "n": 3}
    assert "object object" in probes[3]["payload"]["obj"]
    assert not [r for r in rows if r["kind"] in ("log", "x" * 1000)]

    def exploding(kind: str, **fields: Any) -> None:
        raise OSError("disk full")

    rs.ContextEvidenceSink(exploding, 512).event("probe", {"n": 1})  # swallowed


def test_evidence_reaches_the_context_event_stream(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    make_services(tmp_path, events=events).evidence().event("walk", {"path": "a", "n": 3})
    assert events == [("walk", {"path": "a", "n": 3})]


# --------------------------------------------------------------------------- the lane adapter


WF_LINEAGE = values.Lineage("r_svc_0001", np("a"))


def _issue(lane: svc.AttemptLane, **over: Any) -> svc.AttemptTicket | values.TicketRefusal:
    args: dict[str, Any] = {
        "lineage": WF_LINEAGE,
        "effect": "up",
        "facet": EffectFacetClass.CREATE,
        "repeat": Repeat.SAFE,
        "lifetime": Lifetime.RUN,
        "release": lf.descriptor_record(sup.ARGV),
        "max_attempts": 3,
        "remedy": None,
    }
    args.update(over)
    return lane.issue(**args)


def test_lane_adapter_carries_every_call_through_the_workflow_types(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    lane = services.attempts()
    ident = svc.PlanIdentity(DECL, "a" * 64, (), "o" * 64)
    assert _issue(lane) == values.TicketRefusal.PLAN_NOT_RECORDED  # before the plan
    lane.record_plan(ident)
    with pytest.raises(RuntimeError):
        lane.record_plan(ident)
    ticket = _issue(lane, remedy=values.RemedyGrant("tool.busy", "up", 1))
    assert isinstance(ticket, svc.AttemptTicket)
    assert ticket.lineage == WF_LINEAGE and ticket.attempt == 1
    assert ticket.release == lf.descriptor_record(sup.ARGV)  # the wire mapping
    handle = lane.confirm(
        ticket, values.Confirmation(values.ConfirmationStatus.APPLIED, None, "sel")
    )
    assert isinstance(handle, values.CreatedHandle) and handle.selector == "sel"
    assert handle.lineage == WF_LINEAGE and handle.release == ticket.release
    event = _issue(
        lane,
        effect="test",
        facet=EffectFacetClass.EVENT,
        release={"form": "in_run_group", "helpers_disclosed": False},
    )
    assert isinstance(event, svc.AttemptTicket)
    assert (
        lane.confirm(event, values.Confirmation(values.ConfirmationStatus.APPLIED, None, None))
        is None
    )
    lane.record_result(
        event, values.RecordedResult(False, "test.failed", values.TestCounts(3, 1, 0, 2))
    )
    step = units.StepView(
        lineage=WF_LINEAGE,
        at=sup.WHEN,
        kind=values.StepKind.BLOCKED,
        code="unit.blocked",
        human_action="Fix it.",
        resend=values.Resend.UNKNOWN,
        handle=handle,
    )
    assert lane.record_step(step) is None
    lane.record_released(handle, "release.done")
    end = svc.NodeEnd(
        WF_LINEAGE,
        sup.LATER,
        values.Condition.BLOCKED,
        "unit.blocked",
        "Fix it.",
        values.Resend.UNKNOWN,
        values.Provenance.CREATED,
        svc.Cut.STOPPED,
    )
    assert lane.record_end(end) is None
    assert lane.record_end(end) == svc.LaneRefusal.DUPLICATE  # the first stands

    view = lane.node_record(np("a"))
    assert isinstance(view, units.NodeRecordView)
    up, test = view.tickets
    assert (
        up.handle == handle and up.released_at is not None and up.release_outcome == "release.done"
    )
    assert up.confirmation == values.Confirmation(values.ConfirmationStatus.APPLIED, None, "sel")
    assert up.release == lf.descriptor_record(sup.ARGV)
    assert test.result == values.RecordedResult(False, "test.failed", values.TestCounts(3, 1, 0, 2))
    (held,) = view.steps
    assert held == step  # the NodeEnd is an end, never a step
    assert lane.node_record(np("b")) == units.NodeRecordView((), ())
    assert lane.committed_length() > 0

    # what is on disk is the lane's own record of the same
    read = lf.read_lane(lf.lane_path(services._given.run_dir))
    assert rs.node_record_view(lf.node_record(read.entries, ("a",))) == view


@pytest.mark.parametrize("descriptor", sup.DESCRIPTORS)
def test_release_descriptor_reaches_the_lane_in_every_form(
    descriptor: lf.ReleaseDescriptor,
) -> None:
    wire = lf.descriptor_record(descriptor)
    assert rs.descriptor_to_lane(descriptor) == descriptor
    assert rs.descriptor_to_lane(wire) == descriptor  # the loop's wire mapping
    assert lf.descriptor_record(rs.descriptor_to_lane(wire)) == wire

    # an object of the workflow ports' classes, recognised by name and field names
    fields = {f.name: getattr(descriptor, f.name) for f in dataclasses.fields(descriptor)}
    if isinstance(descriptor, lf.Durable):
        fields["owner"] = descriptor.owner
    mirror = dataclasses.make_dataclass(type(descriptor).__name__, list(fields), frozen=True)(
        **fields
    )
    assert rs.descriptor_to_lane(mirror) == descriptor


def test_unknown_release_descriptor_is_refused_before_any_write(tmp_path: Path) -> None:
    lane = make_services(tmp_path).attempts()
    lane.record_plan(svc.PlanIdentity(DECL, "a" * 64, (), "o" * 64))
    before = lane.committed_length()
    for bad in (object(), {"form": "nope"}, "docker"):
        with pytest.raises(TypeError):
            _issue(lane, release=bad)
    assert lane.committed_length() == before


def test_lane_construction_raises_below_the_reservation(tmp_path: Path) -> None:
    plan = replace(tree_plan(), lane_entries=3)  # < |scope| + 1
    with pytest.raises(ValueError):
        make_services(tmp_path, plan).attempts()


# --------------------------------------------------------------------------- the child process


def _admit(kernel: Any, plugin: str) -> Path:
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args={}))
    assert isinstance(result, AdmitResultAdmitted), result
    return control_support.run_dir_of(kernel, result.run_id)


def _run_child(kernel: Any, run_dir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["TRESTLE_HOME"] = str(kernel.home)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")])
    return subprocess.run(  # noqa: S603
        [sys.executable, "-P", "-m", "trestle.child.main", "--run-dir", str(run_dir)],
        env=env,
        capture_output=True,
        text=True,
        timeout=tolerances.JOIN_WAIT_S * 3,
        check=False,
    )


_PROBE = """

def probe(ctx):
    out = {"has": hasattr(ctx, "run_services")}
    if out["has"]:
        s = ctx.run_services
        adm = s.admitted()
        out.update(
            root=adm.lineage_root,
            digest=adm.accepted.plan_digest,
            declared=adm.accepted.declaration_digest,
            release_point=s.clock().release_point.isoformat(),
            slice_end_root=s.slice_end(importlib.import_module("trestle.workflow.values").NodePath(())).isoformat(),
            scope=sorted(adm.accepted.selected_scope),
        )
    (ctx.outputs / "probe.json").write_text(json.dumps(out))
    return {"name": "x"}
"""


def _probe_source(name: str) -> str:
    source = control_support.workflow_source(name, release_timeouts_s=(2,))
    source = source.replace(
        "from datetime import timedelta",
        "import importlib\nimport json\nfrom datetime import timedelta",
    )
    return source.replace('return {"name": name}', "return probe(ctx)") + _PROBE


def test_workflow_run_binds_services_in_the_child_process(tmp_path: Path) -> None:
    kernel = control_support.make_kernel(tmp_path, {"wfprobe": _probe_source("wfprobe")})
    run_dir = _admit(kernel, "wfprobe")
    done = _run_child(kernel, run_dir)
    assert done.returncode == 0, done.stderr
    probe = json.loads((run_dir / "work" / "outputs" / "probe.json").read_text("utf-8"))
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text("utf-8"))
    assert probe["has"] is True
    assert probe["root"] == run_dir.name
    assert probe["digest"] == spec["plan"]["plan_digest"] and probe["declared"] is not None
    assert probe["scope"] == [[]]
    deadline = datetime.fromisoformat(spec["deadline"])
    assert datetime.fromisoformat(probe["release_point"]) == deadline - timedelta(
        seconds=spec["plan"]["release_slice"]
    )
    assert probe["slice_end_root"] == probe["release_point"]
    assert not (run_dir / "evidence" / "child_error.json").exists()


def test_plain_plugin_run_unchanged(tmp_path: Path) -> None:
    source = control_support.ECHO.read_text("utf-8")
    kernel = control_support.make_kernel(tmp_path, {"echo": source})
    result = kernel.control.admission.admit(AdmitRequest(plugin="echo", args={"message": "hi"}))
    assert isinstance(result, AdmitResultAdmitted)
    run_dir = control_support.run_dir_of(kernel, result.run_id)
    done = _run_child(kernel, run_dir)
    assert done.returncode == 0, done.stderr
    evidence = run_dir / "evidence"
    assert json.loads((evidence / "result.json").read_text("utf-8")) is not None
    assert (evidence / "result.index").exists()
    assert not (evidence / "child_error.json").exists()
    assert not lf.lane_path(run_dir).exists()  # a plain plugin never opens the lane


def test_a_plan_that_does_not_verify_stops_the_child_before_the_plugin(tmp_path: Path) -> None:
    kernel = control_support.make_kernel(tmp_path, {"wfprobe": _probe_source("wfprobe")})
    run_dir = _admit(kernel, "wfprobe")
    spec_path = run_dir / "evidence" / "spec.json"
    spec = json.loads(spec_path.read_text("utf-8"))
    spec["plan"]["release_slice"] = spec["plan"]["release_slice"] + 1  # digest no longer matches
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    done = _run_child(kernel, run_dir)
    assert done.returncode == 1
    record = json.loads((run_dir / "evidence" / "child_error.json").read_text("utf-8"))
    assert record["code"] == codes.DECLARATION_STALE and record["phase"] == "admitted"
    assert not (run_dir / "work" / "outputs" / "probe.json").exists()
