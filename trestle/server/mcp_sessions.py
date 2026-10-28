"""Change 2a: when an agent's MCP session has ended, and the record of it.

fastmcp 3.4.7 and mcp 1.30.0 give no hook for a session's end (every path ends in the SDK's
private `_discard_session`), so the serving process watches its own ASGI stack (`SessionWatch`)
and mirrors the SDK's idle rule using only its public constants. A session S this server opened
ended at the first of:

- `deleted`: a DELETE carrying S was answered 2xx (marked before the answer is forwarded);
- `expired`: S had no request in flight for `SDK_IDLE_TIMEOUT_S + EXPIRY_GUARD_S`, or a request
  carrying S was answered 404;
- `server_gone`: the server that opened S is not running (computed by readers, never written).

A dropped connection is not an end. The record is `home/servers/<server_id>.sessions.json`, written
whole by the server that opened the sessions and by nothing else, holding only ended (dead) session
ids. It is information and cancel scope only: it changes nothing about a run. Named
`mcp_sessions` because `trestle/ops/sessions.py` (the ops console's runs) is another thing.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.streamable_http import MCP_SESSION_ID_HEADER
from mcp.server.streamable_http_manager import DEFAULT_SESSION_IDLE_TIMEOUT

from trestle.common.fsutil import atomic_write_json
from trestle.server.home import (
    Ownership,
    is_locked,
    marked_run_dir,
    owner_lock_path,
    read_marker,
    servers_dir,
)
from trestle.server.ledger import STATE_FILE, TERMINAL_KINDS, evidence_dir

SDK_IDLE_TIMEOUT_S = float(DEFAULT_SESSION_IDLE_TIMEOUT)
# Trestle marks a session only after the SDK has discarded it: the SDK's countdown starts a moment
# before the middleware's count reaches zero, so a later request on a marked id gets 404.
EXPIRY_GUARD_S = 15.0
# An ended entry no owned live run names is dropped after this: it covers a `run` admitted on the
# admission thread while its session's DELETE was being answered.
ENDED_KEEP_S = 60.0

DELETED = "deleted"
EXPIRED = "expired"
SERVER_GONE = "server_gone"

_HEADER = MCP_SESSION_ID_HEADER.lower().encode("latin-1")


def sessions_path(home: Path, server_id: str) -> Path:
    return servers_dir(home) / f"{server_id}.sessions.json"


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SessionEnd:
    at: str | None
    reason: str


def _read_ended(path: Path) -> dict[str, Any]:
    """The `ended` table of a sessions file; an absent or unreadable file reads as empty."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ended = loaded.get("ended") if isinstance(loaded, dict) else None
    return ended if isinstance(ended, dict) else {}


def _end_of(entry: object) -> SessionEnd | None:
    if not isinstance(entry, dict):
        return None
    at = entry.get("at")
    reason = entry.get("reason")
    return SessionEnd(at if isinstance(at, str) else None, str(reason or EXPIRED))


def owner_running(run_dir: Path | None) -> bool | None:
    """True when the run's own owner lock is held (its server is running), else None: the reader
    then asks the owner's server lock. A kernel that admits runs without serving holds owner locks
    but no server lock, and a finished run's owner lock is free while its server runs on, so
    neither lock alone says whether the server that opened a session is gone."""
    if run_dir is not None and is_locked(owner_lock_path(run_dir)):
        return True
    return None


def session_end(
    home: Path, owner: object, session: object, *, owner_alive: bool | None = None
) -> SessionEnd | None:
    """Whether the MCP session `session`, opened by server `owner`, has ended: None when either is
    not a string or the session has not ended; `SessionEnd(None, SERVER_GONE)` when the owner is
    not running; else the owner's recorded end. Whether the owner runs is `owner_alive` when the
    caller knows it (a run's own owner lock), else whether its server lock is held."""
    return SessionReader(home).end(owner, session, owner_alive=owner_alive)


def session_ended(home: Path, record: dict[str, Any]) -> bool | None:
    """Over a run's state record: None for a terminal run or one with no `caller_session` (admitted
    outside MCP), else whether its session has ended."""
    return SessionReader(home).ended(record)


@dataclass
class SessionReader:
    """`session_end` for one query or one doctor report: each owner's file and lock probe is read
    once."""

    home: Path
    _files: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _alive: dict[str, bool] = field(default_factory=dict, repr=False)

    def end(
        self, owner: object, session: object, *, owner_alive: bool | None = None
    ) -> SessionEnd | None:
        if not isinstance(owner, str) or not isinstance(session, str) or not owner or not session:
            return None
        alive = owner_alive if owner_alive is not None else self._alive.get(owner)
        if alive is None:
            alive = is_locked(servers_dir(self.home) / f"{owner}.lock")
            self._alive[owner] = alive
        if not alive:
            return SessionEnd(None, SERVER_GONE)
        ended = self._files.get(owner)
        if ended is None:
            ended = _read_ended(sessions_path(self.home, owner))
            self._files[owner] = ended
        return _end_of(ended.get(session))

    def ended(self, record: dict[str, Any], run_dir: Path | None = None) -> bool | None:
        """Over a run's state record; with `run_dir`, a held owner lock says its server runs
        (`owner_running`)."""
        if record.get("state") in TERMINAL_KINDS:
            return None
        session = record.get("caller_session")
        if not isinstance(session, str) or not session:
            return None
        return (
            self.end(record.get("owner"), session, owner_alive=owner_running(run_dir)) is not None
        )


def _caller_session_of(home: Path, run_id: str) -> str | None:
    run_dir = marked_run_dir(home, run_id, read_marker(home, run_id))
    if run_dir is None:
        return None
    try:
        state = json.loads((evidence_dir(run_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    session = state.get("caller_session") if isinstance(state, dict) else None
    return session if isinstance(session, str) else None


@dataclass
class _Open:
    inflight: int = 0
    idle_since: float = 0.0
    # opened by an `initialize` answer this server sent, or confirmed by a later answer below 400;
    # a sid first seen on a request (a defensive add) that is then answered 404 was never ours
    confirmed: bool = False


@dataclass
class McpSessions:
    """The serving process's watch over the MCP sessions it opened, and the only writer of its
    `servers/<server_id>.sessions.json`. Thread safe: the event loop (the middleware) and the
    reaper thread (`sweep`) both call it."""

    home: Path
    server_id: str
    ownership: Ownership | None = None
    clock: Callable[[], float] = time.monotonic
    _open: dict[str, _Open] = field(default_factory=dict, repr=False)
    # sid -> (the wire entry {at, reason}, the monotonic time it ended)
    _ended: dict[str, tuple[dict[str, str], float]] = field(default_factory=dict, repr=False)
    # run id -> its caller_session (None: admitted outside MCP), read once from its state.json
    _run_sessions: dict[str, str | None] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def path(self) -> Path:
        return sessions_path(self.home, self.server_id)

    # -- the in-memory table ---------------------------------------------------------------------

    def opened(self, sid: str) -> None:
        with self._lock:
            if sid in self._ended:
                return
            entry = self._open.setdefault(sid, _Open(idle_since=self.clock()))
            entry.confirmed = True

    def confirm(self, sid: str) -> None:
        with self._lock:
            entry = self._open.get(sid)
            if entry is not None:
                entry.confirmed = True

    def request_started(self, sid: str) -> None:
        with self._lock:
            if sid in self._ended:
                return
            entry = self._open.get(sid)
            if entry is None:  # defensive: a session whose `initialize` answer was not seen
                entry = self._open[sid] = _Open(idle_since=self.clock())
            entry.inflight += 1

    def request_finished(self, sid: str) -> None:
        with self._lock:
            entry = self._open.get(sid)
            if entry is None:
                return
            entry.inflight = max(0, entry.inflight - 1)
            if entry.inflight == 0:
                entry.idle_since = self.clock()

    def answered_404(self, sid: str) -> None:
        """A request carrying `sid` was answered 404: the SDK no longer knows it. A session this
        server opened has expired; one it never confirmed is forgotten, with nothing written."""
        with self._lock:
            entry = self._open.get(sid)
            if entry is not None and not entry.confirmed:
                del self._open[sid]
                return
        self.end(sid, EXPIRED)

    # -- the seam --------------------------------------------------------------------------------

    def end(self, sid: str, reason: str) -> bool:
        """Mark `sid` ended with `reason` and rewrite the file. Idempotent; acts only for a session
        this server opened. True when it marked it now. The `on_session_lost` change calls the
        conductor from here for the owned live runs that name `sid`."""
        with self._lock:
            entry = self._open.get(sid)
            if sid in self._ended or entry is None or not entry.confirmed:
                return False
            del self._open[sid]
            self._ended[sid] = ({"at": _now_iso(), "reason": reason}, self.clock())
            self._write_locked()
        return True

    def ended_entry(self, sid: str) -> dict[str, str] | None:
        with self._lock:
            got = self._ended.get(sid)
            return dict(got[0]) if got is not None else None

    # -- the reaper's pass -----------------------------------------------------------------------

    def sweep(self) -> None:
        """End the sessions idle past the SDK's bound plus the guard, then drop each ended entry no
        owned live run names once it ended more than `ENDED_KEEP_S` ago."""
        now = self.clock()
        bound = SDK_IDLE_TIMEOUT_S + EXPIRY_GUARD_S
        with self._lock:
            idle = [
                sid
                for sid, entry in self._open.items()
                if entry.confirmed and entry.inflight == 0 and now - entry.idle_since > bound
            ]
            for sid, entry in list(self._open.items()):
                # a defensive add never answered below 400 was never this server's session
                if not entry.confirmed and entry.inflight == 0 and now - entry.idle_since > bound:
                    del self._open[sid]
        for sid in idle:
            self.end(sid, EXPIRED)
        owned = self.ownership.owned() if self.ownership is not None else []
        named: set[str] = set()
        for run_id in owned:
            if run_id not in self._run_sessions:
                self._run_sessions[run_id] = _caller_session_of(self.home, run_id)
            session = self._run_sessions[run_id]
            if session is not None:
                named.add(session)
        for run_id in set(self._run_sessions) - set(owned):
            del self._run_sessions[run_id]
        with self._lock:
            drop = [
                sid
                for sid, (_, at) in self._ended.items()
                if sid not in named and now - at > ENDED_KEEP_S
            ]
            if not drop:
                return
            for sid in drop:
                del self._ended[sid]
            self._write_locked()

    def _write_locked(self) -> None:
        if not self._ended:
            self.path.unlink(missing_ok=True)
            return
        atomic_write_json(
            self.path,
            {
                "v": 1,
                "server_id": self.server_id,
                "ended": {sid: dict(entry) for sid, (entry, _) in sorted(self._ended.items())},
            },
        )


def _header(headers: Any) -> str | None:
    for name, value in headers or ():
        if bytes(name).lower() == _HEADER:
            return bytes(value).decode("latin-1")
    return None


class SessionWatch:
    """ASGI middleware, the innermost of the MCP app's list (behind the bearer token, so an
    unauthenticated request never reaches it). A request carrying a session id counts as in
    flight while the app answers it; its answer's status marks a DELETE 2xx `deleted` and a 404
    `expired`, before the answer is forwarded, so a client that has seen the DELETE's 200 always
    finds the mark. An answer below 400 with a session id response header (the `initialize`
    answer) opens that session."""

    def __init__(self, app: Any, sessions: McpSessions | None = None) -> None:
        self.app = app
        self.sessions = sessions

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        sessions = self.sessions
        if sessions is None or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        sid = _header(scope.get("headers"))
        method = scope.get("method")

        async def watched_send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                status = int(message.get("status") or 0)
                if sid is not None:
                    if method == "DELETE" and 200 <= status < 300:
                        await asyncio.to_thread(sessions.end, sid, DELETED)
                    elif status == 404:
                        await asyncio.to_thread(sessions.answered_404, sid)
                    elif status < 400:
                        sessions.confirm(sid)
                else:
                    new_sid = _header(message.get("headers"))
                    if new_sid is not None and status < 400:
                        sessions.opened(new_sid)
            await send(message)

        if sid is None:
            await self.app(scope, receive, watched_send)
            return
        sessions.request_started(sid)
        try:
            await self.app(scope, receive, watched_send)
        finally:
            sessions.request_finished(sid)
