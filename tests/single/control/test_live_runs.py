"""Live-run visibility (T1, T3, T2): while a run is in flight its run view and `query(view=run)`
carry its latest progress, `run_events` and `run_artifacts` page (marked `live`), an attach under
a name already attached replaces it (one current row, the old id still fetchable, no new count
slot), and a waiting call relays new progress as MCP progress notifications with a heartbeat."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, tolerances
from trestle.child.context import RuntimeContext
from trestle.common import codes
from trestle.common.limits import CaptureLimits
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.ledger import run_dir_for
from trestle.server.progress_relay import ProgressRelay

LIVE_STATUS_SOURCE = """\
import time
from pathlib import Path

from trestle.plugin.surface import Context, trestle


@trestle(deadline=120)
def live_status(
    ctx: Context, gate: str = "", rounds: int = 400, outputs: int = 0
) -> dict[str, int]:
    n = 0
    for n in range(1, rounds + 1):
        ctx.progress(f"monitor round {n}", fraction=n / rounds)
        status = ctx.tmp / "status.md"
        status.write_text(f"| round |\\n| {n} |\\n", encoding="utf-8")
        ctx.attach(status, name="status.md")
        if gate and Path(gate).exists():
            break
        time.sleep(0.05)
    for i in range(outputs):
        (ctx.outputs / f"o{i:02d}.txt").write_text(str(i), encoding="utf-8")
    return {"rounds": n}
"""


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    assert isinstance(kernel.control.publish_plugin(LIVE_STATUS_SOURCE), PublishView)
    return kernel


def _until(predicate, what: str):  # type: ignore[no-untyped-def]
    limit = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < limit:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _query(kernel, view: str, run_id: str) -> dict[str, Any] | RequestOutcome:  # type: ignore[no-untyped-def]
    return kernel.control.query(view, {"run_id": run_id})


def _round_of(text: str) -> int:
    return int(text.strip().splitlines()[-1].strip("| "))


def _fetch_text(kernel, handle: str) -> str:  # type: ignore[no-untyped-def]
    fetched = kernel.control.fetch(handle, {"kind": "head", "count": 10})
    assert isinstance(fetched, dict) and fetched.get("tag") == "text", fetched
    return "\n".join(fetched["lines"])


def test_a_live_run_shows_progress_events_and_one_current_artifact(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    gate = tmp_path / "gate"
    started = kernel.control.run(plugin="live_status", args={"gate": str(gate)}, wait_ms=0)
    assert isinstance(started, RunView), started
    run_id = started.run_id

    def progressed() -> RunView | None:
        view = kernel.control.project.status(run_id)
        assert isinstance(view, RunView), view
        if view.progress is None:
            return None
        return view if _round_of_message(view.progress["message"]) >= 3 else None

    view = _until(progressed, "three rounds of progress")
    assert view.state not in {"succeeded", "failed"}
    assert view.progress is not None
    assert view.progress["message"].startswith("monitor round ")
    assert isinstance(view.progress["fraction"], float)
    assert view.progress["at"]
    assert view.to_dict()["progress"] == view.progress

    # T1: query(view=run) carries it, and the event pager answers while the run is in flight
    run_page = _query(kernel, "run", run_id)
    assert isinstance(run_page, dict), run_page
    assert run_page["items"][0]["progress"]["message"].startswith("monitor round ")
    events = _query(kernel, "run_events", run_id)
    assert isinstance(events, dict), events
    assert events["live"] is True
    assert any(row["kind"] == "progress" for row in events["items"])
    # the views that need the final state stay gated
    for gated in ("last_error", "run_provenance", "run_tail"):
        refused = _query(kernel, gated, run_id)
        assert isinstance(refused, RequestOutcome), (gated, refused)
        assert refused.code == codes.NOT_FINALIZED

    # T3: one current status.md row, whose handle fetches the newest table before finalization
    artifacts = _query(kernel, "run_artifacts", run_id)
    assert isinstance(artifacts, dict), artifacts
    assert artifacts["live"] is True
    assert [row["name"] for row in artifacts["items"]] == ["status.md"]
    live_round = _round_of(_fetch_text(kernel, artifacts["items"][0]["artifact_id"]))
    assert live_round >= 3

    gate.touch()
    ended = kernel.control.project.await_terminal(run_id)
    assert isinstance(ended, RunView) and ended.state == "succeeded", ended
    assert ended.progress is None and "progress" not in ended.to_dict()
    final_rounds = ended.summary["rounds"] if isinstance(ended.summary, dict) else None

    # after the run: still a single current status.md, holding the last round; no live marker
    artifacts = _query(kernel, "run_artifacts", run_id)
    assert isinstance(artifacts, dict), artifacts
    assert "live" not in artifacts
    (current,) = artifacts["items"]
    assert current["name"] == "status.md"
    assert _round_of(_fetch_text(kernel, current["artifact_id"])) == final_rounds
    run_page = _query(kernel, "run", run_id)
    assert isinstance(run_page, dict) and run_page["items"][0]["progress"] is None

    # superseded versions are not listed but still fetch
    events_path = run_dir_for(kernel.home, run_id) / "evidence" / "events.ndjson"
    attached = [
        row
        for row in map(json.loads, events_path.read_text("utf-8").splitlines())
        if row.get("kind") == "artifact_available"
    ]
    assert len(attached) == final_rounds
    assert attached[0].get("supersedes") is None
    assert all(
        row["supersedes"] == prev["artifact_id"]
        for prev, row in zip(attached, attached[1:], strict=False)
    )
    assert _round_of(_fetch_text(kernel, attached[0]["artifact_id"])) == 1


def _round_of_message(message: str) -> int:
    return int(message.rsplit(" ", 1)[-1])


def test_replacing_an_attachment_takes_no_count_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the test limits (10 artifacts) twelve re-attaches of one name and nine outputs all
    fit: the replacements hold one slot between them, and promotion counts only current ones."""
    monkeypatch.setenv("TRESTLE_TEST_LIMITS", "1")
    kernel = _kernel(tmp_path)
    view = kernel.control.run(
        plugin="live_status", args={"rounds": 12, "outputs": 9}, wait_ms=0, completion="bounded"
    )
    assert isinstance(view, RunView), view
    ended = kernel.control.project.await_terminal(view.run_id)
    assert isinstance(ended, RunView) and ended.state == "succeeded", ended
    assert not [m for m in ended.limits_exceeded or [] if m.get("stream") == "artifacts"]
    artifacts = _query(kernel, "run_artifacts", view.run_id)
    assert isinstance(artifacts, dict), artifacts
    names = [row["name"] for row in artifacts["items"]]
    assert names == ["status.md", *(f"o{i:02d}.txt" for i in range(9))]


def test_context_attach_replaces_by_name(tmp_path: Path) -> None:
    work, evidence = tmp_path / "work", tmp_path / "evidence"
    work.mkdir()
    evidence.mkdir()
    events = evidence / "events.ndjson"
    limits = CaptureLimits.from_env()
    ctx = RuntimeContext(
        work=work, evidence=evidence, deadline=_far(), events_path=events, limits=limits
    )
    path = work / "status.md"
    ids = []
    for n in range(limits.max_artifact_count + 5):
        path.write_text(f"round {n}", encoding="utf-8")
        ids.append(ctx.attach(path, name="status.md"))
    assert all(ids) and len(set(ids)) == len(ids)
    assert ctx.limits_markers() == []
    rows = [json.loads(line) for line in events.read_text("utf-8").splitlines()]
    assert [row.get("supersedes") for row in rows] == [None, *ids[:-1]]


def _far():  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    return datetime.now(tz=UTC) + timedelta(hours=1)


def _view(run_id: str, message: str | None, at: str = "t", state: str = "running") -> RunView:
    progress = None if message is None else {"message": message, "fraction": 0.5, "at": at}
    return RunView(run_id=run_id, state=state, progress=progress)


def test_relay_sends_new_progress_once_and_a_heartbeat_after_silence() -> None:
    sent: list[tuple[float, str]] = []
    clock = [0.0]

    async def send(progress: float, message: str) -> None:
        sent.append((progress, message))

    relay = ProgressRelay(send, heartbeat_s=15.0, now=lambda: clock[0])

    async def drive() -> None:
        await relay([_view("r_a", "monitor round 1")])
        await relay([_view("r_a", "monitor round 1")])  # unchanged: not sent again
        clock[0] = 5.0
        await relay([_view("r_a", "monitor round 2", at="t2"), _view("r_b", None)])
        clock[0] = 19.0
        await relay([_view("r_a", "monitor round 2", at="t2"), _view("r_b", None)])  # quiet 14 s
        clock[0] = 20.5
        await relay([_view("r_a", "monitor round 2", at="t2"), _view("r_b", None, state="queued")])

    asyncio.run(drive())
    assert sent == [
        (1.0, "r_a: monitor round 1 (50%)"),
        (2.0, "r_a: monitor round 2 (50%)"),
        (3.0, "waiting: r_a running, r_b queued"),
    ]


def test_relay_survives_a_failed_send() -> None:
    async def send(progress: float, message: str) -> None:
        raise ConnectionError("client went away")

    relay = ProgressRelay(send)
    asyncio.run(relay([_view("r_a", "monitor round 1")]))  # no raise


def test_waits_hand_each_poll_to_the_observer(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    gate = tmp_path / "gate"
    seen: list[str] = []

    async def observe(views: list[RunView]) -> None:
        for view in views:
            if view.progress is not None:
                seen.append(view.progress["message"])
                if len(seen) >= 3:
                    gate.touch()

    async def drive() -> RunView | RequestOutcome:
        return await kernel.control.run_async(
            plugin="live_status",
            args={"gate": str(gate)},
            wait_ms=1000,
            completion="terminal",
            observe=observe,
        )

    ended = asyncio.run(drive())
    assert isinstance(ended, RunView) and ended.state == "succeeded", ended
    assert len(seen) >= 3 and all(m.startswith("monitor round ") for m in seen)
