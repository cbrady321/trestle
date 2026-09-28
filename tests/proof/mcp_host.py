"""Minimal MCP stdio test host (L.P0-0b.2; MC-12 core).

Speaks the wire protocol directly — newline-delimited JSON-RPC 2.0 over one
`trestle serve` subprocess's stdin/stdout, with its own `TRESTLE_HOME` —
deliberately independent of the `mcp`/`fastmcp` client libraries so this
host's view of `tools/list` (raw bytes) can be checked against the
fastmcp client's parsed view (SA-12) rather than assumed identical.

Counts every JSON-RPC request this host sends (`request_count`), including
the `initialize` handshake and the `notifications/initialized` notice,
against exactly this subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
        self._closed = False
        self._initialize()

    def _seed_fixture_plugins(self) -> None:
        src = REPO / "tests" / "fixtures" / "plugins"
        for path in src.glob("*.py"):
            dest = self.home / "plugins" / path.name
            if not dest.exists():
                dest.write_bytes(path.read_bytes())

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

    def _recv_line(self) -> bytes:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else b""
            raise RuntimeError(f"trestle serve closed stdout unexpectedly: {err!r}")
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
        self._recv_line()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # -- public surface ---------------------------------------------------

    def request_count(self) -> int:
        return self._request_count

    def tools_list_raw(self) -> bytes:
        """The exact bytes of the `tools/list` response line."""
        req_id = self._call_id()
        self._send({"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}})
        return self._recv_line()

    def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        req_id = self._call_id()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args or {}},
            }
        )
        raw = self._recv_line()
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
