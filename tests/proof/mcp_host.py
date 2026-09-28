"""Minimal MCP stdio test host (L.P0-0b.2, L.P0-0b.3; MC-12).

Speaks the wire protocol directly — newline-delimited JSON-RPC 2.0 over one
`trestle serve` subprocess's stdin/stdout, with its own `TRESTLE_HOME` —
deliberately independent of the `mcp`/`fastmcp` client libraries so this
host's view of `tools/list` (raw bytes) can be checked against the
fastmcp client's parsed view (SA-12) rather than assumed identical.

Counts every JSON-RPC request this host sends (`request_count`), including
the `initialize` handshake but not the `notifications/initialized` notice
(a notification carries no "id" and gets no response).

A background reader thread demultiplexes responses by "id" so requests can
be pipelined: `hold()` sends one and returns its id without waiting, other
calls can flow on the same connection meanwhile, and `join()` collects the
held response whenever it lands (Assumption 2 — count, hold, sever,
rejoin). `sever()` breaks the session three ways (a JSON-RPC cancellation
notice, closing stdin, or SIGKILL); `rejoin()` opens a fresh session on the
same `TRESTLE_HOME`; `kill_server()`/`restart()` take the whole server down
and bring a new one up on that same home (recovery runs on start, same as
any `trestle serve` boot).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "2025-06-18"


class McpHost:
    def __init__(self, home: Path | None = None, *, timeout_s: float = 15.0) -> None:
        self.home = home if home is not None else Path(tempfile.mkdtemp(prefix="trestle-mcp-host-"))
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "plugins").mkdir(exist_ok=True)
        self._seed_fixture_plugins()

        self.timeout_s = timeout_s
        self._next_id = 1
        self._request_count = 0
        self._responses: dict[int, queue.Queue[bytes]] = {}
        self._responses_lock = threading.Lock()
        self._closed = False

        self._start_process()
        self._initialize()

    def _seed_fixture_plugins(self) -> None:
        src = REPO / "tests" / "fixtures" / "plugins"
        for path in src.glob("*.py"):
            dest = self.home / "plugins" / path.name
            if not dest.exists():
                dest.write_bytes(path.read_bytes())

    # -- process lifecycle ------------------------------------------------

    def _start_process(self) -> None:
        env = os.environ.copy()
        env["TRESTLE_HOME"] = str(self.home)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "trestle.cli", "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        with self._responses_lock:
            self._responses.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

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
            msg_id = message.get("id")
            if msg_id is None:
                continue  # a notification from the server; none expected today
            with self._responses_lock:
                q = self._responses.setdefault(msg_id, queue.Queue(maxsize=1))
            q.put(line)

    # -- wire -----------------------------------------------------------

    def _call_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def _send(self, payload: dict[str, Any]) -> bytes:
        assert self.proc.stdin is not None
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        self.proc.stdin.flush()
        if "id" in payload:  # a notification (no "id") is not a request
            self._request_count += 1
        return line

    def _await_response(self, req_id: int, *, timeout: float | None = None) -> bytes:
        with self._responses_lock:
            q = self._responses.setdefault(req_id, queue.Queue(maxsize=1))
        try:
            line = q.get(timeout=timeout if timeout is not None else self.timeout_s)
        finally:
            with self._responses_lock:
                self._responses.pop(req_id, None)
        return line

    def _initialize(self) -> None:
        req_id = self._call_id()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "tests.proof.mcp_host", "version": "0"},
                },
            }
        )
        self._await_response(req_id)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @staticmethod
    def _parse_call_result(raw: bytes, name: str) -> Any:
        message = json.loads(raw)
        if "error" in message:
            raise RuntimeError(f"{name}: {message['error']}")
        result = message["result"]
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return block["text"]
        return result

    # -- public surface ---------------------------------------------------

    def request_count(self) -> int:
        return self._request_count

    def tools_list_raw(self) -> bytes:
        """The exact bytes of the `tools/list` response line."""
        req_id = self._call_id()
        self._send({"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}})
        return self._await_response(req_id)

    def hold(self, name: str, args: dict[str, Any] | None = None) -> int:
        """Send a `tools/call` request and return its id without waiting
        for a response — the response is withheld on this host's side
        until a caller `join()`s that id, while other requests keep
        flowing on the same connection meanwhile."""
        req_id = self._call_id()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args or {}},
            }
        )
        return req_id

    def join(self, req_id: int, *, timeout: float | None = None) -> Any:
        """Collect the response to a request started by `hold()` (or by
        `call()`'s own internal id, though `call()` already does this)."""
        raw = self._await_response(req_id, timeout=timeout)
        return self._parse_call_result(raw, name=f"request {req_id}")

    def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        req_id = self.hold(name, args)
        return self.join(req_id)

    def sever(self, mode: str, *, req_id: int | None = None) -> None:
        """Break this session.

        - "cancel_notification": send a JSON-RPC `notifications/cancelled`
          for `req_id` (the server may or may not stop the underlying
          work; the run is expected to reach a terminal state on its own
          either way).
        - "stdin_close": close stdin; the server sees EOF on its read loop.
        - "sigkill": kill the subprocess outright.
        """
        if mode == "cancel_notification":
            if req_id is None:
                raise ValueError("cancel_notification requires req_id")
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": req_id},
                }
            )
        elif mode == "stdin_close":
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        elif mode == "sigkill":
            self.proc.kill()
        else:
            raise ValueError(f"unknown sever mode: {mode!r}")

    def kill_server(self) -> None:
        """Kill the subprocess outright (no graceful shutdown)."""
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)

    def restart(self) -> None:
        """Spawn a new `trestle serve` subprocess on the same
        `TRESTLE_HOME` (recovery runs on start, same as any `trestle
        serve` boot) and redo the handshake."""
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()
        self._next_id = 1
        self._request_count = 0
        self._closed = False
        self._start_process()
        self._initialize()

    def close(self) -> None:
        """Terminate the subprocess this host started. Every caller that
        constructs an `McpHost` must reach this (directly or via the `with`
        form) so no server process outlives its test."""
        if self._closed:
            return
        self._closed = True
        if self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()

    def __enter__(self) -> McpHost:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def rejoin(old: McpHost) -> McpHost:
    """A new session on `old`'s `TRESTLE_HOME`. `old` is left exactly as it
    is — sever it first if the point is testing a session against a
    server that is already gone."""
    return McpHost(home=old.home)
