"""L.SV-5.1: `Context.event(kind, **fields)` (BFD-10, MC-15): an additive Protocol method that
`RuntimeContext` delegates to its one `_emit`, with the reserved kinds refused."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from trestle.child.context import RESERVED_EVENT_KINDS, RuntimeContext
from trestle.common.limits import CaptureLimits
from trestle.common.redact import Scrubber
from trestle.plugin import surface

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "tests" / "fixtures" / "golden" / "s0" / "context_surface.json"


def _make(tmp_path: Path, limits: CaptureLimits | None = None, **kwargs: Any) -> RuntimeContext:
    from datetime import UTC, datetime, timedelta

    evidence = tmp_path / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        work=tmp_path / "work",
        evidence=evidence,
        deadline=datetime.now(UTC) + timedelta(seconds=60),
        events_path=evidence / "events.ndjson",
        limits=limits,
        **kwargs,
    )


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "evidence" / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _markers(ctx: RuntimeContext) -> list[dict[str, object]]:
    return ctx.limits_markers()


def _methods(cls: type) -> dict[str, str]:
    return {
        name: str(inspect.signature(member))
        for name, member in vars(cls).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }


def test_protocol_event_additive(tmp_path: Path) -> None:
    golden = json.loads(GOLDEN.read_text("utf-8"))["context"]
    methods = _methods(surface.Context)
    # every S0 member is still there, signature for signature (WR-COMPAT-5)
    for name, signature in golden["methods"].items():
        assert methods[name] == signature
    for name, annotation in golden["attributes"].items():
        assert surface.Context.__annotations__[name] == annotation
    # and event is the only method added
    assert set(methods) - set(golden["methods"]) == {"event"}
    assert set(surface.Context.__annotations__) == set(golden["attributes"])
    # the implementation carries the same signature as the Protocol
    assert (
        inspect.signature(RuntimeContext.event).parameters.keys()
        == inspect.signature(surface.Context.event).parameters.keys()
    )
    ctx = _make(tmp_path)
    assert ctx.event("checkpoint", step=3, name="x") is None
    (row,) = _events(tmp_path)
    assert row["kind"] == "checkpoint"
    assert row["payload"] == {"step": 3, "name": "x"}
    assert "at" in row


def test_event_bounded_by_emit_limits(tmp_path: Path) -> None:
    # single-event size: the same limit and the same marker as log
    single = CaptureLimits(max_single_event_bytes=200)
    ctx = _make(tmp_path / "a", single)
    ctx.event("big", blob="x" * 1000)
    ctx.log("y" * 1000)
    assert _events(tmp_path / "a") == []
    (marker,) = _markers(ctx)
    assert (marker["stream"], marker["limit"]) == ("events", "max_single_event_bytes")
    assert marker["bytes_suppressed"] > 2000  # both writes were counted in the one tally
    # event count is shared with log and progress
    counted = CaptureLimits(max_event_count=2)
    ctx = _make(tmp_path / "b", counted)
    ctx.log("one")
    ctx.event("two")
    ctx.event("three")
    ctx.progress("four")
    assert [r["kind"] for r in _events(tmp_path / "b")] == ["log", "two"]
    (marker,) = _markers(ctx)
    assert marker["limit"] == "max_event_count"
    # total bytes
    sized = CaptureLimits(max_event_bytes=120)
    ctx = _make(tmp_path / "c", sized)
    for i in range(10):
        ctx.event("k", n=i, pad="p" * 20)
    assert 0 < len(_events(tmp_path / "c")) < 10
    assert _markers(ctx)[0]["limit"] == "max_event_bytes"
    # rate
    rated = CaptureLimits(max_events_per_second=3)
    ctx = _make(tmp_path / "d", rated)
    for i in range(10):
        ctx.event("k", n=i)
    assert len(_events(tmp_path / "d")) <= 4
    assert _markers(ctx)[0]["limit"] == "max_events_per_second"


def test_event_is_scrubbed_like_log(tmp_path: Path) -> None:
    ctx = _make(tmp_path, scrubber=Scrubber(secrets=frozenset({"hunter2-secret"})))
    ctx.event("note", detail="the value hunter2-secret leaks")
    raw = (tmp_path / "evidence" / "events.ndjson").read_text("utf-8")
    assert "hunter2-secret" not in raw
    assert _events(tmp_path)[0]["kind"] == "note"


@pytest.mark.parametrize("kind", sorted(RESERVED_EVENT_KINDS))
def test_reserved_kinds_refused(tmp_path: Path, kind: str) -> None:
    ctx = _make(tmp_path)
    with pytest.raises(ValueError):
        ctx.event(kind, message="forged")
    assert _events(tmp_path) == []
    assert _markers(ctx) == []  # a refusal is not a limit drop


def test_reserved_set_names_the_runtime_and_lane_kinds() -> None:
    assert {"log", "progress", "artifact_available", "error"} <= RESERVED_EVENT_KINDS
    assert {"plan", "issue", "confirmation", "result", "released", "step", "node_end"} <= (
        RESERVED_EVENT_KINDS
    )


@pytest.mark.parametrize("kind", ["", None, 3])
def test_bad_kind_refused(tmp_path: Path, kind: object) -> None:
    ctx = _make(tmp_path)
    with pytest.raises(ValueError):
        ctx.event(kind, x=1)  # type: ignore[arg-type]
    assert _events(tmp_path) == []


def test_unreserved_kinds_are_written_including_near_misses(tmp_path: Path) -> None:
    ctx = _make(tmp_path)
    for kind in ("Log", "errors", "step_done", "progress2"):
        ctx.event(kind)
    assert [r["kind"] for r in _events(tmp_path)] == ["Log", "errors", "step_done", "progress2"]


def test_pack_context_structural_subset() -> None:
    from trestle_packs.core.artifacts import PackContext

    pack, full = _methods(PackContext), _methods(surface.Context)
    assert pack  # non-vacuous
    for name, signature in pack.items():
        assert full[name] == signature  # a Context satisfies PackContext, signature for signature
    assert "event" not in pack  # PackContext is not widened (BFD-50 Leave)
    assert set(PackContext.__annotations__) <= set(surface.Context.__annotations__)
