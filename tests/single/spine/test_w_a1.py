"""L.SV-5.9: the one-vertex spine fixture and the W-A1 variants, each in exactly one
`run(completion="terminal")` call through the MC-12 MCP host, the same tools an agent calls.

`spine_leaf` (tests/fixtures/workflows/spine_leaf.py) is a published workflow plugin, written
against the public unit-author surface of `trestle.workflow` (`PUBLIC_MODULES`), whose declared tree
is one leaf over the fakes of `trestle_packs.fakes`: skip (the postcondition already holds),
advance-and-poll (create once, poll until ready, release on done), cancel mid-poll and deadline
mid-poll (the marker never turns ready; a stop ends the wait and the created marker is released).
The variants are the spine gate's parametrization (TM-B2-3 `one-vertex-spine-fixture`).

The lane is read through the proof court's own oracle (`tests.proof.records`), never the product's
reader. Every timing bound comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05).
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import mcp_host, records, tolerances
from trestle.common import clock, codes

pytestmark = pytest.mark.spine

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "workflows"
# The spine gate's parametrization (TM-B2-3): exactly this tuple, read by the register probe
# `python -m tests.proof.probes.single one-vertex-spine-fixture` by its literal value.
SPINE_FIXTURES = ("spine_leaf",)
FIXTURE = SPINE_FIXTURES[0]
COMPOSITE_FIXTURES = ("probe_all_root", "probe_choice_root")
EVENT_KIND = "spine_observed"  # the fixture's evidence event, one per observation
CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
# the fixture's declared deadline (plugin decorator and entry) and a shorter one for the variant
# that lets the deadline end the wait: the root budget plus the release slice must fit it
DECLARED_DEADLINE_S = 120
SHORT_DEADLINE_S = 20
# a call that waits for its terminal answer waits out the deadline and the finalization margin
HOST_TIMEOUT_S = float(SHORT_DEADLINE_S) + clock.finalization_margin + tolerances.JOIN_WAIT_S


def _fixture_source(deadline_s: int) -> str:
    source = (FIXTURES / f"{FIXTURE}.py").read_text(encoding="utf-8")
    source = source.replace(f"deadline={DECLARED_DEADLINE_S}", f"deadline={deadline_s}")
    return source.replace(f"seconds={DECLARED_DEADLINE_S}", f"seconds={deadline_s}")


@contextmanager
def _host(tmp_path: Path, *, deadline_s: int = DECLARED_DEADLINE_S) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        (host.home / "plugins" / f"{FIXTURE}.py").write_text(
            _fixture_source(deadline_s), encoding="utf-8"
        )
        yield host


def _run_dir(host: mcp_host.McpHost, run_id: str) -> Path:
    (found,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    return found


def _terminal(host: mcp_host.McpHost, mode: str, *, wait_s: float = 0) -> dict[str, Any]:
    """The one call: the terminal answer to a `spine_leaf` run."""
    wait_ms = int((wait_s or tolerances.HARNESS_WAIT_MS / 1000) * 1000)
    answer = host.call(
        "run",
        {
            "plugin": FIXTURE,
            "args": {"env": "dev", "mode": mode},
            "wait_ms": wait_ms,
            "completion": "terminal",
        },
    )
    assert isinstance(answer, dict), answer
    return answer


def _classes(run_dir: Path) -> list[str]:
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return [row.cls for row in lane.rows]


def _entries(run_dir: Path, cls: str) -> list[dict[str, Any]]:
    return [row.entry for row in records.lane_rows(run_dir).rows if row.cls == cls]


def _observations(run_dir: Path) -> list[dict[str, Any]]:
    events = run_dir / "evidence" / "events.ndjson"
    if not events.exists():
        return []
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    return [row["payload"] for row in rows if row["kind"] == EVENT_KIND]


def _wait_for(predicate: Any, what: str) -> None:
    assert support.wait_until(predicate, tolerances.JOIN_WAIT_S), what


def _issued(run_dir: Path) -> bool:
    return "confirmation" in {row.cls for row in records.lane_rows(run_dir).rows}


def _released_after(run_dir: Path, effect: str) -> list[dict[str, Any]]:
    return [e for e in _entries(run_dir, "released") if e["effect"] == effect]


def _assert_created_marker_released(run_dir: Path) -> None:
    """The created marker's cooperative release: the stop was issued after the vertex's NodeEnd
    and the `released` entry was written after its observed absence (V-4.4, V-10.4)."""
    classes = _classes(run_dir)
    assert classes.count("end") == 1
    end_at = classes.index("end")
    stops = [
        i
        for i, row in enumerate(records.lane_rows(run_dir).rows)
        if row.cls == "issue" and row.entry["effect"] == STOP_EFFECT
    ]
    assert len(stops) == 1 and stops[0] > end_at, classes
    assert len(_released_after(run_dir, CREATE_EFFECT)) == 1, classes
    assert classes[-1] == "released", classes


# ---- one call, passed -----------------------------------------------------------------------


def test_one_call_passed(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        answer = _terminal(host, "advance")
        assert answer["state"] == "succeeded", answer
        assert answer["outcome"]["class"] == "passed", answer
        assert answer["answer"]["outcome"] == "passed"
        assert answer["answer"]["primary"]["path"] == []
        assert answer["answer"]["cleanup"]["clean"] is True
        run_dir = _run_dir(host, answer["run_id"])
        # the answer follows the finalized terminal row, never the reverse
        assert support.kinds(run_dir)[-2:] == ["evidence_finalized", "succeeded"]


# ---- skip -----------------------------------------------------------------------------------


def test_skip(tmp_path: Path) -> None:
    """The postcondition already holds (a found instance): no claim, no effect, no release."""
    with _host(tmp_path) as host:
        answer = _terminal(host, "skip")
        assert answer["state"] == "succeeded" and answer["outcome"]["class"] == "passed", answer
        run_dir = _run_dir(host, answer["run_id"])
        assert _classes(run_dir) == ["plan", "end"]
        (end,) = _entries(run_dir, "end")
        assert (end["condition"], end["provenance"], end["cut"]) == ("satisfied", "found", None)
        assert answer["answer"]["cleanup"]["clean"] is True
        assert not _entries(run_dir, "issue")


# ---- advance and poll -----------------------------------------------------------------------


def test_advance_and_poll(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        answer = _terminal(host, "advance")
        assert answer["state"] == "succeeded" and answer["outcome"]["class"] == "passed", answer
        run_dir = _run_dir(host, answer["run_id"])
        # one advance: one create claim, never re-issued to wait (WR-VERIFY-7)
        creates = [e for e in _entries(run_dir, "issue") if e["effect"] == "up"]
        assert len(creates) == 1 and creates[0]["attempt"] == 1
        # at least one poll: an observation after the create that found it present and not ready
        polls = [o for o in _observations(run_dir) if o["selector_present"] and not o["ready"]]
        assert polls, "the wait polled at least once"
        (end,) = _entries(run_dir, "end")
        assert (end["condition"], end["provenance"]) == ("satisfied", "created")
        # the created marker is released on done, with its `released` entry
        _assert_created_marker_released(run_dir)


# ---- a stop ends the wait -------------------------------------------------------------------


def _stopped_within_bound(host: mcp_host.McpHost, run_dir: Path, run_id: str) -> None:
    """A stop reaches the loop as a flag: the wait ends, the created marker is released by the
    loop itself (the loop's stop, not the host's kill), and the terminal row follows inside the
    stop bound."""
    classes = _classes(run_dir)
    assert classes.count("end") == 1
    (end,) = _entries(run_dir, "end")
    assert end["cut"] in ("stopped", None), end
    _assert_created_marker_released(run_dir)
    (stop,) = support.rows_of(run_dir, "stop_row")
    assert stop["lane_committed_length"] is not None
    assert not support.alive_marked(support.marked(run_id), run_id)


def test_cancel_mid_poll(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        started = host.call(
            "run",
            {"plugin": FIXTURE, "args": {"env": "dev", "mode": "hang"}, "wait_ms": 0},
        )
        run_id = started["run_id"]
        run_dir = _run_dir(host, run_id)
        with support.reaping(run_id):
            _wait_for(lambda: _issued(run_dir), "the marker was never created")
            _wait_for(lambda: len(_observations(run_dir)) >= 3, "the wait never polled")
            asked = time.monotonic()
            assert host.call("cancel", {"run_id": run_id})["code"] == codes.CANCEL_ACCEPTED
            view = host.call(
                "await_runs",
                {"run_ids": [run_id], "mode": "all", "timeout_ms": int(HOST_TIMEOUT_S * 1000)},
            )
            views = view["result"] if isinstance(view, dict) and "result" in view else view
            (answer,) = views
            waited = time.monotonic() - asked
            assert answer["state"] == "cancelled", answer
            assert answer["outcome"]["class"] == "cancelled", answer
            # the wait ended inside the cooperative release slice: the loop released the marker
            # itself, so the run was over before the host had to kill anything
            assert waited < clock.release_slice + tolerances.SETTLE_LONG_S, waited
            _stopped_within_bound(host, run_dir, run_id)
            (stop,) = support.rows_of(run_dir, "stop_row")
            assert stop["cause"] == "cancel"


def test_deadline_mid_poll(tmp_path: Path) -> None:
    with _host(tmp_path, deadline_s=SHORT_DEADLINE_S) as host:
        started = time.monotonic()
        answer = _terminal(host, "stall", wait_s=SHORT_DEADLINE_S + clock.finalization_margin)
        elapsed = time.monotonic() - started
        run_id = answer["run_id"]
        run_dir = _run_dir(host, run_id)
        with support.reaping(run_id):
            assert answer["state"] == "timed_out", answer
            assert answer["outcome"]["class"] == "timed_out", answer
            # the answer arrives inside deadline plus finalization margin, and the wait ended at
            # the release point (deadline - slice), not at the deadline
            assert elapsed <= SHORT_DEADLINE_S + clock.finalization_margin
            _stopped_within_bound(host, run_dir, run_id)
            (stop,) = support.rows_of(run_dir, "stop_row")
            assert stop["cause"] == "release_point"
            (end,) = _entries(run_dir, "end")
            assert end["at"] <= _first_released_at(run_dir)


def _first_released_at(run_dir: Path) -> str:
    released = _entries(run_dir, "released")
    assert released
    return str(released[0]["released_at"])


# ---- plan identity, composite roots, vertex count --------------------------------------------


@pytest.mark.proves(
    "WR-PLAN-3", "WR-PLAN-3:identity-before-first-effect", "A", "single", "PROC", "CI"
)
def test_plan_identity_before_first_effect(tmp_path: Path) -> None:
    """Read as a record fact after the run: the plan entry is the lane's first entry, and it
    precedes the first claim of any effect (B1-I8)."""
    with _host(tmp_path) as host:
        answer = _terminal(host, "advance")
        run_dir = _run_dir(host, answer["run_id"])
        classes = _classes(run_dir)
        assert classes[0] == "plan" and classes.count("plan") == 1, classes
        assert classes.index("plan") < classes.index("issue"), classes
        (plan,) = _entries(run_dir, "plan")
        spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
        assert plan["declaration_digest"] == spec["plan"]["declaration_digest"]


@pytest.mark.proves(
    "WR-DEADLINE-4", "WR-DEADLINE-4:A-wait-ends-on-stop", "A", "single", "PROC", "CI"
)
def test_wait_ends_on_stop_read_from_the_record(tmp_path: Path) -> None:
    """A cancel mid-poll ends the wait: after the stop row there is no observation beyond the
    loop's release pass, and the vertex's NodeEnd precedes every release-walk entry."""
    with _host(tmp_path) as host:
        started = host.call(
            "run",
            {"plugin": FIXTURE, "args": {"env": "dev", "mode": "hang"}, "wait_ms": 0},
        )
        run_id = started["run_id"]
        run_dir = _run_dir(host, run_id)
        with support.reaping(run_id):
            _wait_for(lambda: len(_observations(run_dir)) >= 3, "the wait never polled")
            assert host.call("cancel", {"run_id": run_id})["code"] == codes.CANCEL_ACCEPTED
            _wait_for(
                lambda: "cancelled" in support.kinds(run_dir),
                "the run never reached its terminal row",
            )
            counted = len(_observations(run_dir))
            time.sleep(tolerances.SETTLE_LONG_S)
            assert len(_observations(run_dir)) == counted, "the wait kept polling after the stop"
            classes = _classes(run_dir)
            assert classes.index("end") < classes.index("released"), classes


def test_composite_roots_refused_temp_code(tmp_path: Path) -> None:
    """A-1 admits one vertex: a composite root is refused with the temporary code before any run
    id, through the same one call (TM-B2-1, DM-07)."""
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        for name in COMPOSITE_FIXTURES:
            shutil.copy(FIXTURES / f"{name}.py", host.home / "plugins")
            refused = host.call(
                "run",
                {
                    "plugin": name,
                    "wait_ms": tolerances.HARNESS_WAIT_MS,
                    "completion": "terminal",
                },
            )
            assert refused["code"] == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED, refused
        assert not list((host.home / "runs").glob("*/*")), "a refused root left a run directory"


def test_plan_vertex_count_is_one(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        answer = _terminal(host, "skip")
        run_dir = _run_dir(host, answer["run_id"])
        spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
        vertices = spec["plan"]["vertices"]
        assert [v["path"] for v in vertices] == [""]
        assert vertices[0]["compose"] == "leaf" and vertices[0]["children"] == []
        assert spec["plan"]["edges"] == []
