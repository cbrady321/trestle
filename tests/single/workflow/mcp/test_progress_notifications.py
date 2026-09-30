"""L.SL-2.3: progress never completes a call and never solicits input (WR-TERM-9; BFD-22, D-l).

A workflow reports progress through `ctx.progress`; through the MC-12 host that is all a caller can
see of it. The falsifier runs a real workflow (the spine leaf, with progress calls added to its
plugin callable, one of them worded as a request for input) behind one `run(completion="terminal")`
call and watches everything the server sends the host:

* while the run is live and its progress is already recorded, the call has no response yet;
* the host receives no server-initiated request at all (0 elicitation requests: an elicitation is
  a server-to-client request), and any notification it receives is not a response to the call;
* the call's one response is the terminal answer (`state` succeeded, class passed), the same
  answer a plain run gives, and it carries no request for input.

The spine leaf is unchanged on disk: the plugin source is derived from it here."""

from __future__ import annotations

import json
import queue
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import mcp_host, tolerances

REPO = Path(__file__).resolve().parents[4]
SPINE_LEAF = REPO / "tests" / "fixtures" / "workflows" / "spine_leaf.py"
CALL = '    run_tree(ctx, ENTRY, {"env": env, "mode": mode}, ports=ports)\n'
# Progress before the run, then a gate the test opens (so the run is provably live while the test
# looks at the host), the run itself, and a last progress after it. The first message is worded as a
# request for input on purpose: progress is text for a person to read, never a question.
PROGRESS_CALLS = """    ctx.progress("Enter your API key to continue?", fraction=0.1)
    (ctx.tmp / "ready").write_text("1", encoding="utf-8")
    gate = ctx.tmp / "go"
    for _ in range(400):
        if gate.exists():
            break
        time.sleep(0.05)
    ctx.progress("marker created", fraction=0.5)
    run_tree(ctx, ENTRY, {"env": env, "mode": mode}, ports=ports)
    ctx.progress("done", fraction=1.0)
"""
TERMINAL_KINDS = frozenset({"succeeded", "failed", "timed_out", "cancelled", "interrupted"})


class RecordingHost(mcp_host.McpHost):
    """The MC-12 host that also keeps every message the server originates: a request (it carries a
    `method` and an `id`, the shape of `elicitation/create` and `sampling/createMessage`) or a
    notification (a `method` and no `id`). The stock host drops them and would file a server
    request under a client id."""

    def __init__(self, home: Path, *, timeout_s: float) -> None:
        self.server_requests: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        super().__init__(home=home, timeout_s=timeout_s)

    def _reader_loop(self) -> None:
        stdout = self.proc.stdout
        assert stdout is not None
        while True:
            try:
                line = stdout.readline()
            except (OSError, ValueError):
                return
            if not line:
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" in message:  # the server speaking first, never a response to a call
                (self.server_requests if "id" in message else self.notifications).append(message)
                continue
            msg_id = message.get("id")
            if msg_id is None:
                continue
            with self._responses_lock:
                q = self._responses.setdefault(msg_id, queue.Queue(maxsize=1))
            q.put(line)


def _progress_source() -> str:
    source = SPINE_LEAF.read_text(encoding="utf-8")
    assert source.count(CALL) == 1, "the spine leaf's run_tree call moved"
    return source.replace(CALL, PROGRESS_CALLS).replace(
        "from datetime import timedelta\n", "import time\nfrom datetime import timedelta\n", 1
    )


@contextmanager
def _host(tmp_path: Path) -> Iterator[RecordingHost]:
    with RecordingHost(tmp_path / "host-home", timeout_s=tolerances.JOIN_WAIT_S * 3) as host:
        (host.home / "plugins" / "spine_leaf.py").write_text(_progress_source(), encoding="utf-8")
        yield host


def _run_dir(host: mcp_host.McpHost) -> Path | None:
    found = sorted((host.home / "runs").glob("*/r_*"))
    return found[0] if found else None


def _progress_events(run_dir: Path) -> list[dict[str, Any]]:
    events = run_dir / "evidence" / "events.ndjson"
    if not events.exists():
        return []
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    return [row["payload"] for row in rows if row["kind"] == "progress"]


def _answered(host: mcp_host.McpHost, req_id: int) -> bool:
    """Whether the server has answered request `req_id` yet, without consuming the answer."""
    with host._responses_lock:
        pending = host._responses.get(req_id)
        return pending is not None and not pending.empty()


@pytest.mark.proves("WR-TERM-9", "WR-TERM-9:progress-never-completes", "A", "single", "MCP", "CI")
def test_progress_never_completes_or_solicits(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        req_id = host.hold(
            "run",
            {
                "plugin": "spine_leaf",
                "args": {"env": "dev", "mode": "advance"},
                "wait_ms": int(tolerances.JOIN_WAIT_S * 3 * 1000) - 1000,
                "completion": "terminal",
            },
        )
        # the run is live and has reported progress, and the call is still open
        assert support.wait_until(
            lambda: (d := _run_dir(host)) is not None and (d / "work" / "tmp" / "ready").exists(),
            tolerances.JOIN_WAIT_S,
        ), "the workflow never reported its first progress"
        run_dir = _run_dir(host)
        assert run_dir is not None
        first = _progress_events(run_dir)
        assert first and first[0]["message"] == "Enter your API key to continue?"
        assert set(support.kinds(run_dir)).isdisjoint(TERMINAL_KINDS), "the run is not live"
        assert not _answered(host, req_id), "progress completed the call"
        assert host.server_requests == [], "the server solicited input"

        (run_dir / "work" / "tmp" / "go").write_text("1", encoding="utf-8")
        answer = host.join(req_id)

    # the only answer is the terminal one
    assert isinstance(answer, dict)
    assert answer["state"] == "succeeded" and answer["outcome"]["class"] == "passed", answer
    assert answer["answer"]["outcome"] == "passed"
    assert answer["run_id"] == run_dir.name
    assert not {"elicitation", "input_request", "prompt", "human_action"} & set(answer), answer
    assert answer["answer"].get("human_action") in (None, ""), answer["answer"]
    # 0 elicitation requests (0 server requests of any kind); notifications, if a server sent any,
    # are never a response to the call
    assert host.server_requests == []
    assert all("result" not in n and "error" not in n for n in host.notifications)
    assert not any("elicit" in str(n.get("method", "")).lower() for n in host.notifications)
    # the progress was recorded as evidence, in order, and the run ended by its own terminal row
    messages = [p["message"] for p in _progress_events(run_dir)]
    assert messages == ["Enter your API key to continue?", "marker created", "done"], messages
    assert support.kinds(run_dir)[-2:] == ["evidence_finalized", "succeeded"]
    assert support.kinds(run_dir).count("succeeded") == 1  # one terminal row, however much progress


def test_a_plain_run_answers_the_same_with_and_without_progress(tmp_path: Path) -> None:
    """Progress adds evidence events and nothing to the answer's shape: the terminal answer of the
    progress-emitting workflow and of the unchanged spine leaf have the same keys."""
    with _host(tmp_path / "with") as host:
        req_id = host.hold(
            "run",
            {
                "plugin": "spine_leaf",
                "args": {"env": "dev", "mode": "skip"},
                "wait_ms": int(tolerances.JOIN_WAIT_S * 3 * 1000) - 1000,
                "completion": "terminal",
            },
        )
        assert support.wait_until(
            lambda: (d := _run_dir(host)) is not None and (d / "work" / "tmp" / "ready").exists(),
            tolerances.JOIN_WAIT_S,
        )
        run_dir = _run_dir(host)
        assert run_dir is not None
        (run_dir / "work" / "tmp" / "go").write_text("1", encoding="utf-8")
        with_progress = host.join(req_id)
    plain_source = SPINE_LEAF.read_text(encoding="utf-8")
    with mcp_host.McpHost(home=tmp_path / "plain", timeout_s=tolerances.JOIN_WAIT_S * 3) as plain:
        (plain.home / "plugins" / "spine_leaf.py").write_text(plain_source, encoding="utf-8")
        without = plain.call(
            "run",
            {
                "plugin": "spine_leaf",
                "args": {"env": "dev", "mode": "skip"},
                "wait_ms": tolerances.HARNESS_WAIT_MS,
                "completion": "terminal",
            },
        )
    assert set(with_progress) == set(without)
    assert with_progress["state"] == without["state"] == "succeeded"
    assert set(with_progress["answer"]) == set(without["answer"])
