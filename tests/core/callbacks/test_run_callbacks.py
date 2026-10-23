"""Run callbacks: the test plan of docs/proposals/2026-10-23-run-callbacks-design.md (1-10).

A run sent with `callback="<url>"` gets one POST to that URL once it has ended (version 1 body),
retried on a fixed schedule, recorded in `<run_dir>/callback.json`, and kept across restarts by
`home/callbacks/pending/<run_id>`."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.proof import tolerances
from trestle.common import codes
from trestle.common.fsutil import atomic_write_json
from trestle.common.types import AdmitRequest, RequestOutcome, RunView
from trestle.server import callbacks
from trestle.server.config import load_config
from trestle.server.home import ServerLock
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import create_kernel
from trestle.server.recovery import find_run_dir

HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "plugins"
REPO = HERE.parents[2]
RUN_FIELDS = {
    "run_id",
    "plugin",
    "idempotency_key",
    "state",
    "outcome_class",
    "answer_outcome",
    "error",
    "started_at",
    "ended_at",
}
STATES = {"succeeded", "failed", "cancelled", "timed_out", "worker_exit", "interrupted"}
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
SECRET = "s3cr3t-token-path"
WAIT_MS = tolerances.HARNESS_WAIT_MS
NO_WAIT = 0  # wait_ms=0: return the first frame at once
SHORT_DEADLINE_S = 2  # a run's own deadline, short enough to end it timed_out


# -- the listener -----------------------------------------------------------------------------


class Listener:
    """A test listener on 127.0.0.1: answers each POST with the next scripted status (the last one
    repeats) and records it, with whether the run was finalized when it arrived."""

    def __init__(self, home: Path, statuses: list[int] | None = None, port: int = 0) -> None:
        self.home = home
        self.statuses = list(statuses or [200])
        self.requests: list[dict[str, Any]] = []
        self.delay_s = 0.0
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (http.server's name)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                listener.record(self.path, dict(self.headers.items()), raw)
                if listener.delay_s:
                    time.sleep(listener.delay_s)
                status = listener.next_status()
                self.send_response(status)
                if 300 <= status <= 399:
                    self.send_header("Location", "/redirected")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str = f"/trestle/{SECRET}?k=v") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def next_status(self) -> int:
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def record(self, path: str, headers: dict[str, str], raw: bytes) -> None:
        body = json.loads(raw)
        run_dir = find_run_dir(self.home, body["run"]["run_id"])
        ledger = RunLedger.open(ledger_path(run_dir)) if run_dir is not None else None
        self.requests.append(
            {
                "path": path,
                "headers": headers,
                "raw": raw,
                "body": body,
                "finalized": ledger is not None
                and ledger.terminal_state() is not None
                and ledger.has_kind("evidence_finalized")
                and ledger.records[-1].get("kind") == ledger.terminal_state(),
            }
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixed schedule, shortened (its shape is what is tested)."""
    monkeypatch.setattr(callbacks, "SCHEDULE_S", (0.0, 0.05, 0.1, 0.15, 0.2))
    monkeypatch.setattr(callbacks, "TIMEOUT_S", 2.0)
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def kernel(home: Path, fast: None):
    return create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)


@pytest.fixture
def listener(home: Path) -> Iterator[Listener]:
    made = Listener(home)
    yield made
    made.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(cond, timeout: float = 20.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(tolerances.POLL_FINE_S)
    raise AssertionError("condition not met in time")


def _record(home: Path, run_id: str) -> dict[str, Any]:
    run_dir = find_run_dir(home, run_id)
    assert run_dir is not None
    record = callbacks.read_record(run_dir)
    assert record is not None
    return record


def _closed(home: Path, run_id: str) -> bool:
    run_dir = find_run_dir(home, run_id)
    record = callbacks.read_record(run_dir) if run_dir is not None else None
    return record is not None and record["status"] in callbacks.CLOSED_STATUSES


def _settle(kernel, run_id: str) -> dict[str, Any]:  # noqa: ANN001
    _wait(lambda: _closed(kernel.home, run_id))
    kernel.callbacks.join(5)
    assert not callbacks.marker_path(kernel.home, run_id).exists()
    return _record(kernel.home, run_id)


def _assert_v1(request: dict[str, Any], run_id: str) -> dict[str, Any]:
    body, headers = request["body"], request["headers"]
    assert len(request["raw"]) <= callbacks.MAX_BODY_BYTES
    assert body["type"] == "trestle.run.ended"
    assert body["version"] == 1
    assert body["delivery_id"] == f"{run_id}:ended"
    assert isinstance(body["attempt"], int) and body["attempt"] >= 1
    assert ISO_Z.match(body["sent_at"])
    run = body["run"]
    assert set(run) == RUN_FIELDS
    assert run["run_id"] == run_id
    assert run["state"] in STATES
    assert run["outcome_class"] is None or isinstance(run["outcome_class"], str)
    assert isinstance(run["answer_outcome"], str)
    assert run["error"] is None or set(run["error"]) == {"code", "message"}
    if run["error"] is not None:
        assert len(run["error"]["message"].encode("utf-8")) <= 512
    assert ISO_Z.match(run["ended_at"])
    assert run["started_at"] is None or ISO_Z.match(run["started_at"])
    assert headers["Content-Type"] == "application/json"
    assert headers["Trestle-Event"] == "run.ended"
    assert headers["Trestle-Delivery"] == f"{run_id}:ended"
    assert headers["User-Agent"].startswith("trestle/")
    assert request["finalized"], "the POST arrived before the run's terminal row"
    return run


# -- 1. admission -----------------------------------------------------------------------------


def _nothing_on_disk(home: Path) -> None:
    runs = home / "runs"
    assert not runs.exists() or not any(p for month in runs.iterdir() for p in month.iterdir())
    pending = callbacks.pending_dir(home)
    assert not pending.exists() or not any(pending.iterdir())


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://127.0.0.1/x", codes.INVALID_ARGS),
        ("/relative/path", codes.INVALID_ARGS),
        ("http://user:pw@127.0.0.1:8765/x", codes.INVALID_ARGS),
        ("http://user@127.0.0.1:8765/x", codes.INVALID_ARGS),
        ("http://127.0.0.1:8765/x#frag", codes.INVALID_ARGS),
        ("http://127.0.0.1:8765/" + "a" * 2049, codes.INVALID_ARGS),
        ("http://127.0.0.1:8765/a b", codes.INVALID_ARGS),
        ("http://127.0.0.1:99999/", codes.INVALID_ARGS),
        ("http://example.com/hook", codes.ADMISSION_CALLBACK_NOT_ALLOWED),
        ("https://10.0.0.5:8443/hook", codes.ADMISSION_CALLBACK_NOT_ALLOWED),
        ("http://127.0.0.2:8765/hook", codes.ADMISSION_CALLBACK_NOT_ALLOWED),
        ("http://127.0.0.1:18732/mcp", codes.ADMISSION_CALLBACK_NOT_ALLOWED),
        ("http://localhost:18733/", codes.ADMISSION_CALLBACK_NOT_ALLOWED),
        ("http://[::1]:18732/", codes.ADMISSION_CALLBACK_NOT_ALLOWED),
    ],
)
def test_admission_refuses_before_anything_exists(kernel, url: str, code: str) -> None:
    result = kernel.control.run(plugin="echo", args={}, callback=url)
    assert isinstance(result, RequestOutcome), result
    assert result.code == code
    assert result.retryable is False
    assert result.origin == "admission"
    assert SECRET not in result.message
    _nothing_on_disk(kernel.home)


def test_admission_refuses_a_live_servers_own_port(kernel) -> None:
    lock = ServerLock.acquire(kernel.home, "s_other", port=45123)
    try:
        result = kernel.control.run(plugin="echo", args={}, callback="http://127.0.0.1:45123/hook")
    finally:
        lock.close()
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.ADMISSION_CALLBACK_NOT_ALLOWED
    _nothing_on_disk(kernel.home)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/cb",
        "http://[::1]:8765/cb",
        "http://localhost:8765/cb",
        "https://localhost/cb",
    ],
)
def test_admission_accepts_loopback(kernel, url: str) -> None:
    result = kernel.control.admission.admit(AdmitRequest(plugin="echo", args={}, callback=url))
    assert result.tag == "admitted"
    run_dir = find_run_dir(kernel.home, result.run_id)
    assert run_dir is not None
    created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert created is not None and created["callback"] == url
    assert callbacks.marker_path(kernel.home, result.run_id).exists()
    assert _record(kernel.home, result.run_id)["status"] == "pending"


def test_restricted_profile_same_rules(home: Path, fast: None) -> None:
    (home / "config.toml").write_text(
        '[profile]\nmode = "restricted"\nallowlist = ["echo"]\n', encoding="utf-8"
    )
    kernel = create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)
    refused = kernel.control.run(plugin="echo", args={}, callback="http://example.com/x")
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.ADMISSION_CALLBACK_NOT_ALLOWED


# -- 2. the operator's list -------------------------------------------------------------------


def test_allow_prefix_on_a_slash_boundary() -> None:
    allow = callbacks.parse_allow({"allow": ["https://hooks.example.internal/trestle/"]})
    ok = callbacks.check_url("https://hooks.example.internal/trestle/x", allow, ())
    assert ok.origin == "https://hooks.example.internal"
    for url in (
        "https://hooks.example.internal/trestlex",
        "https://hooks.example.internal/other/x",
        "https://hooks.example.internal:8443/trestle/x",
        "http://hooks.example.internal/trestle/x",
        "https://other.example.internal/trestle/x",
    ):
        with pytest.raises(callbacks.NotAllowed):
            callbacks.check_url(url, allow, ())
    bare = callbacks.parse_allow({"allow": ["https://hooks.example.internal/trestle"]})
    callbacks.check_url("https://hooks.example.internal/trestle", bare, ())
    callbacks.check_url("https://hooks.example.internal/trestle/y", bare, ())
    with pytest.raises(callbacks.NotAllowed):
        callbacks.check_url("https://hooks.example.internal/trestlex", bare, ())


def test_allowlisted_host_is_admitted(home: Path, fast: None) -> None:
    (home / "config.toml").write_text(
        '[callbacks]\nallow = ["https://hooks.example.internal/trestle/"]\n', encoding="utf-8"
    )
    kernel = create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)
    admitted = kernel.control.admission.admit(
        AdmitRequest(plugin="echo", args={}, callback="https://hooks.example.internal/trestle/a")
    )
    assert admitted.tag == "admitted"
    refused = kernel.control.admission.admit(
        AdmitRequest(plugin="echo", args={}, callback="https://hooks.example.internal/trestlex")
    )
    assert refused.tag == "refused"
    assert refused.outcome.code == codes.ADMISSION_CALLBACK_NOT_ALLOWED


@pytest.mark.parametrize(
    "allow",
    [
        '"http://hooks.example.internal/trestle/"',  # http to a host other than loopback
        '"ftp://hooks.example.internal/"',
        '"not a url"',
        '"https://user:pw@hooks.example.internal/"',
        '"https://hooks.example.internal/#x"',
        "42",
    ],
)
def test_bad_allow_entry_stops_the_server(home: Path, allow: str) -> None:
    (home / "config.toml").write_text(f"[callbacks]\nallow = [{allow}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[callbacks\]"):
        load_config(home)
    with pytest.raises(ValueError, match=r"\[callbacks\]"):
        create_kernel(home=home, plugin_dirs=[PLUGINS], skip_recovery=True)


def test_loopback_http_allow_entry_loads(home: Path) -> None:
    (home / "config.toml").write_text(
        '[callbacks]\nallow = ["http://127.0.0.1:9000/x/"]\n', encoding="utf-8"
    )
    assert load_config(home).callback_allow == (
        callbacks.AllowEntry("http", "127.0.0.1", 9000, "/x/"),
    )


# -- 3. one POST per terminal state -----------------------------------------------------------


def _one_post(kernel, listener: Listener, run_id: str) -> dict[str, Any]:  # noqa: ANN001
    record = _settle(kernel, run_id)
    assert record["status"] == "delivered"
    assert len(listener.requests) == 1
    return _assert_v1(listener.requests[0], run_id)


def test_succeeded(kernel, listener: Listener) -> None:
    view = kernel.control.run(
        plugin="echo",
        args={"message": "hi"},
        wait_ms=WAIT_MS,
        completion="terminal",
        idempotency_key="cb:ok",
        callback=listener.url(),
    )
    assert isinstance(view, RunView) and view.state == "succeeded"
    run = _one_post(kernel, listener, view.run_id)
    assert run["state"] == "succeeded"
    assert run["answer_outcome"] == "passed"
    assert run["plugin"] == "echo"
    assert run["idempotency_key"] == "cb:ok"
    assert run["error"] is None
    assert run["started_at"] is not None
    assert listener.requests[0]["path"] == f"/trestle/{SECRET}?k=v"


def test_failed(kernel, listener: Listener) -> None:
    view = kernel.control.run(
        plugin="boom", args={}, wait_ms=WAIT_MS, completion="terminal", callback=listener.url()
    )
    assert isinstance(view, RunView) and view.state == "failed"
    run = _one_post(kernel, listener, view.run_id)
    assert run["state"] == "failed"
    assert run["error"]["code"] == view.error["code"]
    assert run["answer_outcome"] == view.answer["outcome"]
    assert run["outcome_class"] == view.outcome["class"]
    assert run["idempotency_key"] is None


def test_cancelled(kernel, listener: Listener) -> None:
    view = kernel.control.run(
        plugin="slow", args={"seconds": 60.0}, wait_ms=NO_WAIT, callback=listener.url()
    )
    assert isinstance(view, RunView)
    _wait(lambda: kernel.control.project.status(view.run_id).state == "running")
    assert kernel.control.cancel(view.run_id).code == codes.CANCEL_ACCEPTED
    run = _one_post(kernel, listener, view.run_id)
    assert run["state"] == "cancelled"


def test_timed_out(kernel, listener: Listener) -> None:
    view = kernel.control.run(
        plugin="slow",
        args={"seconds": 60.0},
        wait_ms=WAIT_MS,
        completion="terminal",
        deadline_s=SHORT_DEADLINE_S,
        callback=listener.url(),
    )
    assert isinstance(view, RunView) and view.state == "timed_out"
    run = _one_post(kernel, listener, view.run_id)
    assert run["state"] == "timed_out"
    assert run["answer_outcome"] == "timed_out"


def test_worker_exit(kernel, listener: Listener) -> None:
    view = kernel.control.run(
        plugin="exit2", args={}, wait_ms=WAIT_MS, completion="terminal", callback=listener.url()
    )
    assert isinstance(view, RunView) and view.state == "worker_exit"
    run = _one_post(kernel, listener, view.run_id)
    assert run["state"] == "worker_exit"


def test_held_run_ended_by_after_unmet(kernel, listener: Listener) -> None:
    first = kernel.control.run(
        plugin="boom", args={}, wait_ms=WAIT_MS, completion="terminal", idempotency_key="cb:first"
    )
    assert isinstance(first, RunView) and first.state == "failed"
    held = kernel.control.run(
        plugin="echo",
        args={},
        wait_ms=WAIT_MS,
        completion="terminal",
        after={"key": "cb:first", "when": "succeeded"},
        callback=listener.url(),
    )
    assert isinstance(held, RunView) and held.state == "cancelled"
    run = _one_post(kernel, listener, held.run_id)
    assert run["state"] == "cancelled"
    assert run["error"]["code"] == codes.EXECUTION_AFTER_UNMET
    assert run["started_at"] is None


def _kill_server_with_live_run(home: Path, url: str) -> str:
    """Test plan 6: a serving process starts a slow run with `url` as its callback, then is
    SIGKILLed while the run is live. Returns the run id."""
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "serve_and_hang.py"), str(home), str(PLUGINS), url],
        stdout=subprocess.PIPE,
        env=env,
        cwd=REPO,
    )
    try:
        assert proc.stdout is not None
        run_id = proc.stdout.readline().decode().strip()
        assert run_id.startswith("r_"), run_id
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
    return run_id


def _restart(home: Path):  # noqa: ANN202
    """A new server on the home: its start pass reaps the dead owner's run, then it serves."""
    kernel = create_kernel(home=home, plugin_dirs=[PLUGINS])
    kernel.start_service()
    return kernel


def _stop(kernel) -> None:  # noqa: ANN001
    kernel.callbacks.join(10)
    if kernel.reaper is not None:
        kernel.reaper.stop()
    if kernel.server_lock is not None:
        kernel.server_lock.close()


def test_interrupted_by_a_dead_server(home: Path, fast: None, listener: Listener) -> None:
    run_id = _kill_server_with_live_run(home, listener.url())
    assert listener.requests == []
    restarted = _restart(home)
    try:
        record = _settle(restarted, run_id)
    finally:
        _stop(restarted)
    assert record["status"] == "delivered"
    assert len(listener.requests) == 1
    run = _assert_v1(listener.requests[0], run_id)
    assert run["state"] == "interrupted"


# -- 4. retries -------------------------------------------------------------------------------


def _echo_with(kernel, url: str) -> str:  # noqa: ANN001
    view = kernel.control.run(
        plugin="echo", args={}, wait_ms=WAIT_MS, completion="terminal", callback=url
    )
    assert isinstance(view, RunView) and view.state == "succeeded"
    return view.run_id


def test_retries_until_2xx(kernel, listener: Listener) -> None:
    listener.statuses = [503, 503, 200]
    run_id = _echo_with(kernel, listener.url())
    record = _settle(kernel, run_id)
    assert record["status"] == "delivered"
    assert record["attempts"] == 3
    assert record["last_http_status"] == 200
    assert record["delivered_at"] is not None
    assert [r["body"]["attempt"] for r in listener.requests] == [1, 2, 3]
    assert {r["body"]["delivery_id"] for r in listener.requests} == {f"{run_id}:ended"}
    assert {r["headers"]["Trestle-Delivery"] for r in listener.requests} == {f"{run_id}:ended"}


@pytest.mark.parametrize("status", [408, 429, 500, 599])
def test_retryable_statuses(kernel, listener: Listener, status: int) -> None:
    listener.statuses = [status, 204]
    record = _settle(kernel, _echo_with(kernel, listener.url()))
    assert record["status"] == "delivered"
    assert record["attempts"] == 2


def test_404_stops_after_one_attempt(kernel, listener: Listener) -> None:
    listener.statuses = [404, 200]
    record = _settle(kernel, _echo_with(kernel, listener.url()))
    assert record["status"] == "failed"
    assert record["attempts"] == 1
    assert record["last_http_status"] == 404
    assert len(listener.requests) == 1


def test_302_is_not_followed(kernel, listener: Listener) -> None:
    listener.statuses = [302, 200]
    record = _settle(kernel, _echo_with(kernel, listener.url()))
    assert record["status"] == "failed"
    assert record["attempts"] == 1
    assert record["last_http_status"] == 302
    assert [r["path"] for r in listener.requests] == [f"/trestle/{SECRET}?k=v"]


def test_no_listener_five_attempts_then_failed(kernel, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[int] = []
    real_post = callbacks.post

    def counting(target, data, run_id, user_agent):  # noqa: ANN001, ANN202
        sent.append(json.loads(data)["attempt"])
        return real_post(target, data, run_id, user_agent)

    monkeypatch.setattr(callbacks, "post", counting)
    record = _settle(kernel, _echo_with(kernel, f"http://127.0.0.1:{_free_port()}/cb"))
    assert record["status"] == "failed"
    assert record["attempts"] == 5
    assert record["last_http_status"] is None
    assert record["last_error"]
    assert sent == [1, 2, 3, 4, 5]


def test_schedule_and_timeout_are_the_contracts() -> None:
    assert callbacks.SCHEDULE_S == (0.0, 2.0, 10.0, 30.0, 120.0)
    assert callbacks.TIMEOUT_S == 5.0
    assert callbacks.ABANDON_AFTER_S == 24 * 3600.0


# -- 5. the status, and no path in any view ---------------------------------------------------


def _all_views(kernel, run_id: str, key: str) -> str:  # noqa: ANN001
    control = kernel.control
    parts: list[Any] = [control.project.status(run_id).to_dict()]
    for view, params in (
        ("run", {"run_id": run_id}),
        ("recent_runs", {}),
        ("run_provenance", {"run_id": run_id}),
        ("run_events", {"run_id": run_id}),
        ("run_by_key", {"idempotency_key": key}),
        ("status", {}),
    ):
        result = control.query(view, params)
        parts.append(result.to_dict() if isinstance(result, RequestOutcome) else result)
    return json.dumps(parts)


@pytest.mark.parametrize(
    ("statuses", "status", "attempts"),
    [([200], "delivered", 1), ([503, 200], "delivered", 2), ([404], "failed", 1)],
)
def test_status_shown_and_path_never(
    kernel, listener: Listener, statuses: list[int], status: str, attempts: int
) -> None:
    listener.statuses = statuses
    view = kernel.control.run(
        plugin="echo",
        args={},
        wait_ms=WAIT_MS,
        completion="terminal",
        idempotency_key="cb:views",
        callback=listener.url(),
    )
    assert isinstance(view, RunView)
    record = _settle(kernel, view.run_id)
    assert record["status"] == status and record["attempts"] == attempts
    expected = {
        "origin": f"http://127.0.0.1:{listener.port}",
        "status": status,
        "attempts": attempts,
        "last_http_status": statuses[-1],
        "last_error": None if status == "delivered" else "http 404",
        "delivered_at": record["delivered_at"],
    }
    assert kernel.control.project.status(view.run_id).to_dict()["callback"] == expected
    row = kernel.control.query("run", {"run_id": view.run_id})["items"][0]
    assert row["callback"] == expected
    dumped = _all_views(kernel, view.run_id, "cb:views")
    assert SECRET not in dumped and "k=v" not in dumped


def test_pending_status_while_running(kernel, listener: Listener) -> None:
    view = kernel.control.run(
        plugin="slow", args={"seconds": 60.0}, wait_ms=NO_WAIT, callback=listener.url()
    )
    assert isinstance(view, RunView)
    _wait(lambda: kernel.control.project.status(view.run_id).state == "running")
    live = kernel.control.project.status(view.run_id).to_dict()
    assert live["callback"]["status"] == "pending"
    assert live["callback"]["attempts"] == 0
    row = kernel.control.query("run", {"run_id": view.run_id})["items"][0]
    assert row["callback"]["status"] == "pending"
    assert SECRET not in json.dumps([live, row])
    kernel.control.cancel(view.run_id)
    _settle(kernel, view.run_id)


def test_no_callback_no_field(kernel) -> None:
    view = kernel.control.run(plugin="echo", args={}, wait_ms=WAIT_MS, completion="terminal")
    assert isinstance(view, RunView)
    assert "callback" not in view.to_dict()
    assert kernel.control.query("run", {"run_id": view.run_id})["items"][0]["callback"] is None
    assert not callbacks.pending_dir(kernel.home).exists()


# -- 6. restart with the listener down --------------------------------------------------------


def test_restart_delivers_after_sigkill(home: Path, fast: None) -> None:
    port = _free_port()
    url = f"http://127.0.0.1:{port}/trestle/{SECRET}"
    run_id = _kill_server_with_live_run(home, url)  # the listener is down throughout
    assert callbacks.marker_path(home, run_id).exists()
    listener = Listener(home, port=port)
    try:
        restarted = _restart(home)
        try:
            record = _settle(restarted, run_id)
            view = restarted.control.project.status(run_id)
        finally:
            _stop(restarted)
    finally:
        listener.close()
    assert isinstance(view, RunView) and view.state == "interrupted"
    assert record["status"] == "delivered"
    assert view.to_dict()["callback"]["status"] == "delivered"
    assert len(listener.requests) == 1
    assert _assert_v1(listener.requests[0], run_id)["state"] == "interrupted"


# -- 7. restart mid-retries, two servers, the 24-hour cut-off ---------------------------------


def _ended_run_pending(kernel, url: str) -> str:  # noqa: ANN001
    """A run that has ended with its callback still pending (as if its sender died)."""
    admitted = kernel.control.admission.admit(AdmitRequest(plugin="echo", args={}, callback=url))
    assert admitted.tag == "admitted"
    marker = callbacks.marker_path(kernel.home, admitted.run_id)
    hidden = marker.with_name(".hidden")
    os.rename(marker, hidden)  # the owner's kick finds no marker: nothing is sent yet
    kernel.control._hand_off(admitted.run_id, "echo")
    _wait(
        lambda: (
            isinstance(view := kernel.control.project.status(admitted.run_id), RunView)
            and view.state == "succeeded"
        )
    )
    kernel.callbacks.join(5)
    os.rename(hidden, marker)
    return admitted.run_id


def test_restart_mid_retries_continues_to_five(kernel, listener: Listener) -> None:
    listener.statuses = [503]
    run_id = _ended_run_pending(kernel, listener.url())
    run_dir = find_run_dir(kernel.home, run_id)
    assert run_dir is not None
    # two attempts were made before the server died
    record = {
        **callbacks.read_record(run_dir),
        "attempts": 2,
        "last_http_status": 503,
        "last_attempt_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    atomic_write_json(callbacks.callback_path(run_dir), record)
    restarted = create_kernel(home=kernel.home, plugin_dirs=[PLUGINS], skip_recovery=True)
    restarted.callbacks.scan()
    record = _settle(restarted, run_id)
    assert record["status"] == "failed"
    assert record["attempts"] == 5
    assert [r["body"]["attempt"] for r in listener.requests] == [3, 4, 5]


def test_two_servers_exactly_one_sends(kernel, listener: Listener) -> None:
    listener.delay_s = 0.5
    run_id = _ended_run_pending(kernel, listener.url())
    first = callbacks.CallbackSender(kernel.home)
    second = callbacks.CallbackSender(kernel.home)
    results: list[str | None] = []
    threads = [
        threading.Thread(target=lambda s=s: results.append(s.send(run_id))) for s in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert results.count("delivered") == 1 and results.count(None) == 1
    assert len(listener.requests) == 1
    assert not callbacks.marker_path(kernel.home, run_id).exists()


def test_marker_older_than_24_hours_is_abandoned(
    kernel, listener: Listener, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _ended_run_pending(kernel, listener.url())
    later = (datetime.now(tz=UTC) + timedelta(hours=24, minutes=1)).timestamp()
    monkeypatch.setattr(callbacks, "_wall", lambda: later)
    assert callbacks.CallbackSender(kernel.home).send(run_id) == "abandoned"
    assert listener.requests == []
    assert _record(kernel.home, run_id)["status"] == "abandoned"
    assert not callbacks.marker_path(kernel.home, run_id).exists()


def test_orphan_marker_is_removed_only_when_old(kernel) -> None:
    marker = callbacks.marker_path(kernel.home, "r_nonexistent")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    sender = callbacks.CallbackSender(kernel.home)
    assert sender.send("r_nonexistent") is None
    assert marker.exists()  # an admission may be about to make it visible
    old = time.time() - callbacks.ORPHAN_AFTER_S - 10
    os.utime(marker, (old, old))
    assert sender.send("r_nonexistent") is None
    assert not marker.exists()


# -- 8. idempotency ---------------------------------------------------------------------------


def test_join_keeps_the_original_callback(kernel, home: Path) -> None:
    a, b = Listener(home), Listener(home)
    try:
        first = kernel.control.run(
            plugin="slow",
            args={"seconds": 1.0},
            wait_ms=NO_WAIT,
            idempotency_key="cb:join",
            callback=a.url("/a"),
        )
        assert isinstance(first, RunView)
        joined = kernel.control.run(
            plugin="slow",
            args={"seconds": 1.0},
            wait_ms=WAIT_MS,
            completion="terminal",
            idempotency_key="cb:join",
            callback=b.url("/b"),
        )
        assert isinstance(joined, RunView)
        assert joined.run_id == first.run_id
        record = _settle(kernel, first.run_id)
        again = kernel.control.run(
            plugin="slow",
            args={"seconds": 1.0},
            wait_ms=WAIT_MS,
            idempotency_key="cb:join",
            callback=b.url("/b"),
        )
        assert isinstance(again, RunView) and again.run_id == first.run_id
        kernel.callbacks.join(5)
        time.sleep(tolerances.SETTLE_S)
    finally:
        a.close()
        b.close()
    assert record["origin"] == f"http://127.0.0.1:{a.port}"
    run_dir = find_run_dir(home, first.run_id)
    created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert created["callback"] == a.url("/a")
    assert len(a.requests) == 1
    assert b.requests == []


# -- 9. localhost is not looked up ------------------------------------------------------------


def test_localhost_connects_without_dns(
    kernel, listener: Listener, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[object] = []
    real = socket.getaddrinfo

    def stub(host, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if host == "localhost":
            looked_up.append(host)
            return real("10.255.255.1", *args, **kwargs)  # would send it elsewhere
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", stub)
    monkeypatch.setattr(socket, "gethostbyname", lambda host: looked_up.append(host) or "10.0.0.1")
    run_id = _echo_with(kernel, f"http://localhost:{listener.port}/trestle/{SECRET}")
    record = _settle(kernel, run_id)
    assert record["status"] == "delivered"
    assert record["origin"] == f"http://localhost:{listener.port}"
    assert looked_up == []
    assert listener.requests[0]["headers"]["Host"] == f"localhost:{listener.port}"


# -- 10. the docs -----------------------------------------------------------------------------


def test_docs_describe_callbacks() -> None:
    from trestle.server import authoring

    agents = (REPO / "docs" / "agents.md").read_text(encoding="utf-8")
    guide = authoring.guide_topic("callbacks")
    for text in (agents, guide):
        assert "callback=" in text
        assert "trestle.run.ended" in text
        assert '"version": 1' in text
        assert "at-least-once" in text.lower()
        assert "delivery_id" in text
        assert "admission.callback_not_allowed" in text
    assert "trestle://guide/plugins/callbacks" in authoring.guide_root()
    assert "`callbacks`" in authoring.plugin_guide()
    console = (REPO / "docs" / "agent-console-mcp.md").read_text(encoding="utf-8")
    assert "callback" in console and "admission.callback_not_allowed" in console
    security = (REPO / "docs" / "security.md").read_text(encoding="utf-8")
    assert "[callbacks]" in security
