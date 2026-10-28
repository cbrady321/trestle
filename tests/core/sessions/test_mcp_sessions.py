"""Change 2a: the session watch (`trestle.server.mcp_sessions`) on a fake monotonic clock, its
ASGI middleware, the per-server file and its readers, and the pin on the SDK's idle timeout."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from trestle.server import mcp_sessions as ms
from trestle.server.home import ServerLock, live_servers, servers_dir

SERVER = "s_test0000000000"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Owned:
    def __init__(self, *run_ids: str) -> None:
        self.ids = list(run_ids)

    def owned(self) -> list[str]:
        return list(self.ids)


@pytest.fixture
def home(tmp_path: Path) -> Any:
    root = tmp_path / "home"
    root.mkdir()
    lock = ServerLock.acquire(root, SERVER)
    yield root
    lock.close()


def _watch(home: Path, clock: Clock, owned: Owned | None = None) -> ms.McpSessions:
    return ms.McpSessions(home, SERVER, owned, clock=clock)  # type: ignore[arg-type]


def _file(home: Path) -> dict[str, Any]:
    return json.loads(ms.sessions_path(home, SERVER).read_text(encoding="utf-8"))


BOUND = ms.SDK_IDLE_TIMEOUT_S + ms.EXPIRY_GUARD_S


def test_idle_bound_plus_guard(home: Path) -> None:
    clock = Clock()
    watch = _watch(home, clock)
    watch.opened("a")
    watch.request_started("a")
    watch.request_finished("a")
    clock.t += BOUND - 1
    watch.sweep()
    assert ms.session_end(home, SERVER, "a") is None
    assert not ms.sessions_path(home, SERVER).exists()
    clock.t += 2
    watch.sweep()
    end = ms.session_end(home, SERVER, "a")
    assert end is not None and end.reason == ms.EXPIRED and end.at is not None
    assert _file(home)["ended"]["a"]["reason"] == "expired"
    assert _file(home)["v"] == 1 and _file(home)["server_id"] == SERVER


def test_open_get_stream_survives_the_sweep(home: Path) -> None:
    clock = Clock()
    watch = _watch(home, clock)
    watch.opened("a")
    watch.request_started("a")  # a GET stream, never finished
    clock.t += BOUND * 10
    watch.sweep()
    assert ms.session_end(home, SERVER, "a") is None


def _app(status: int, *, new_sid: str | None = None) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        headers = [(b"mcp-session-id", new_sid.encode())] if new_sid else []
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    return app


def _call(watch: ms.McpSessions, app: Any, method: str, sid: str | None) -> list[Any]:
    """Drive the middleware; record, at each message forwarded, whether the file names `sid`."""
    seen: list[Any] = []
    headers = [(b"mcp-session-id", sid.encode())] if sid else []
    scope = {"type": "http", "method": method, "headers": headers}

    async def receive() -> Any:
        return {"type": "http.request", "body": b""}

    async def send(message: Any) -> None:
        path = ms.sessions_path(watch.home, SERVER)
        marked = path.exists() and sid is not None and sid in _file(watch.home)["ended"]
        seen.append((message["type"], marked))

    asyncio.run(ms.SessionWatch(app, sessions=watch)(scope, receive, send))
    return seen


def test_initialize_answer_opens_and_delete_marks_before_the_forward(home: Path) -> None:
    clock = Clock()
    watch = _watch(home, clock)
    _call(watch, _app(200, new_sid="a"), "POST", None)
    seen = _call(watch, _app(200), "DELETE", "a")
    assert seen[0] == ("http.response.start", True)  # written before the 200 is forwarded
    end = ms.session_end(home, SERVER, "a")
    assert end is not None and end.reason == ms.DELETED


def test_404_on_a_known_sid_expires_it_and_on_an_unknown_one_writes_nothing(home: Path) -> None:
    clock = Clock()
    watch = _watch(home, clock)
    _call(watch, _app(404), "POST", "ghost")
    assert not ms.sessions_path(home, SERVER).exists()
    _call(watch, _app(200, new_sid="a"), "POST", None)
    _call(watch, _app(404), "POST", "a")
    end = ms.session_end(home, SERVER, "a")
    assert end is not None and end.reason == ms.EXPIRED


def test_an_error_answer_does_not_open_a_session(home: Path) -> None:
    watch = _watch(home, Clock())
    _call(watch, _app(401, new_sid="x"), "POST", None)
    assert watch.end("x", ms.DELETED) is False


def test_prune_keeps_an_entry_while_an_owned_live_run_names_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    owned = Owned("r_1")
    sessions = {"r_1": "a"}
    monkeypatch.setattr(ms, "_caller_session_of", lambda _home, run_id: sessions.get(run_id))
    watch = _watch(home, clock, owned)
    watch.opened("a")
    watch.opened("b")
    assert watch.end("a", ms.DELETED) and watch.end("b", ms.DELETED)
    clock.t += ms.ENDED_KEEP_S - 1
    watch.sweep()
    assert set(_file(home)["ended"]) == {"a", "b"}
    clock.t += 2
    watch.sweep()
    assert set(_file(home)["ended"]) == {"a"}  # r_1 still names it
    owned.ids = []
    watch.sweep()
    assert not ms.sessions_path(home, SERVER).exists()  # empty: removed


def test_a_dead_owner_reads_server_gone(tmp_path: Path) -> None:
    end = ms.session_end(tmp_path, "s_gone", "a")
    assert end == ms.SessionEnd(None, ms.SERVER_GONE)
    assert ms.session_end(tmp_path, None, "a") is None
    assert ms.session_end(tmp_path, "s_gone", None) is None


def test_session_ended_over_a_state_record(home: Path) -> None:
    watch = _watch(home, Clock())
    watch.opened("a")
    record = {"state": "running", "owner": SERVER, "caller_session": "a"}
    assert ms.session_ended(home, record) is False
    watch.end("a", ms.DELETED)
    assert ms.session_ended(home, record) is True
    assert ms.session_ended(home, {**record, "state": "succeeded"}) is None
    assert ms.session_ended(home, {**record, "caller_session": None}) is None


def test_live_servers_prune_removes_a_dead_servers_file(tmp_path: Path) -> None:
    root = servers_dir(tmp_path)
    root.mkdir(parents=True)
    (root / "s_dead.lock").write_text("{}")
    (root / "s_dead.sessions.json").write_text("{}")
    assert live_servers(tmp_path, prune=True) == []
    assert not (root / "s_dead.sessions.json").exists()


def test_server_lock_close_removes_its_file(tmp_path: Path) -> None:
    lock = ServerLock.acquire(tmp_path, SERVER)
    watch = _watch(tmp_path, Clock())
    watch.opened("a")
    watch.end("a", ms.DELETED)
    assert ms.sessions_path(tmp_path, SERVER).exists()
    lock.close()
    assert not ms.sessions_path(tmp_path, SERVER).exists()


def test_end_is_idempotent_and_called_once_per_session(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam `on_session_lost` will use: one call per ended session, with its reason."""
    clock = Clock()
    watch = _watch(home, clock)
    calls: list[tuple[str, str]] = []
    original = ms.McpSessions.end

    def spy(self: ms.McpSessions, sid: str, reason: str) -> bool:
        marked = original(self, sid, reason)
        if marked:
            calls.append((sid, reason))
        return marked

    monkeypatch.setattr(ms.McpSessions, "end", spy)
    _call(watch, _app(200, new_sid="a"), "POST", None)
    _call(watch, _app(200, new_sid="b"), "POST", None)
    _call(watch, _app(200), "DELETE", "a")
    _call(watch, _app(404), "POST", "a")  # after the end: no second mark
    assert watch.end("a", ms.EXPIRED) is False
    assert _file(home)["ended"]["a"]["reason"] == "deleted"
    clock.t += BOUND + 1
    watch.sweep()
    watch.sweep()
    assert calls == [("a", ms.DELETED), ("b", ms.EXPIRED)]


def test_sdk_idle_timeout_pin() -> None:
    """fastmcp 3.4.7 passes no idle timeout, so the SDK's default is in force, and the watch mirrors
    it. Fails if fastmcp starts passing its own value or the SDK changes its default."""
    from fastmcp import FastMCP
    from mcp.server.streamable_http_manager import DEFAULT_SESSION_IDLE_TIMEOUT
    from starlette.testclient import TestClient

    app = FastMCP("pin").http_app(transport="streamable-http")
    with TestClient(app):
        (route,) = [r for r in app.routes if getattr(r, "path", None) == "/mcp"]
        manager = route.endpoint.session_manager
        assert manager is not None
        assert manager.session_idle_timeout == DEFAULT_SESSION_IDLE_TIMEOUT
        assert DEFAULT_SESSION_IDLE_TIMEOUT == ms.SDK_IDLE_TIMEOUT_S == 1800
