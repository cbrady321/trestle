"""The agent token: `$TRESTLE_HOME/agent.token`, the bearer token every MCP request over the
loopback HTTP transport must carry (decision record service-and-credentials, Decision 1).

The token is possession of a `0600` file in the user's home: it bounds the MCP port to "a process
that can read the user's home" instead of "a process on the machine". It is not a login and does
not make the service multi-user. The ops port does not use it (it admits nothing).
"""

from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

TOKEN_FILE = "agent.token"
TOKEN_ENV = "TRESTLE_AGENT_TOKEN"


def token_path(home: Path) -> Path:
    return home / TOKEN_FILE


def ensure_token(home: Path) -> tuple[str, bool]:
    """The home's token, created (`0600`, written once with O_EXCL) when the file is missing.
    Returns the token and whether this call created it. Two processes racing here read the same
    file: the loser of the O_EXCL create reads the winner's token."""
    path = token_path(home)
    home.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read_token(home), False
    try:
        os.write(fd, (value + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return value, True


def read_token(home: Path) -> str:
    """The token in the home's file. Raises OSError when it is missing, ValueError when empty."""
    value = token_path(home).read_text(encoding="ascii").strip()
    if not value:
        raise ValueError(f"{token_path(home)} is empty")
    return value


Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class BearerTokenMiddleware:
    """ASGI middleware: every HTTP request must carry `Authorization: Bearer <token>`, or it is
    answered 401 before it reaches the MCP app. There is no setting that turns it off."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = b""
        for name, value in scope.get("headers", ()):
            if name.lower() == b"authorization":
                given = value
                break
        if hmac.compare_digest(given, self._expected):
            await self.app(scope, receive, send)
            return
        body = b'{"error":"unauthorized","message":"missing or wrong bearer token"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b'Bearer realm="trestle"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
