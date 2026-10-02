#!/usr/bin/env python3
"""stub_issuer: the demo credential issuer (MC-B-06, TM-B4-1.3, D-9: AWS is DEMO ONLY, never real).

A stdlib HTTP server on loopback that stands in for "the cloud's token service" for the demo grant.
It holds ONE demo identity and an ordered list of generations it has issued; it serves:

    GET  /host          identity, expires_at, generation, interactive_required   (no secret)
    GET  /credential    the demo token of the current generation (the ONLY route that returns it;
                        the delivery adapter writes it into a consumer's channel file)
    GET  /whoami        one authenticated call: `Authorization: Bearer <token>` ->
                        {"authenticated", "generation"} (the generation the presented token claims)
    GET  /order?a=&b=   the issuer's OWN ordering of two generations it issued: "older" | "same" |
                        "newer" | "unknown" (a is <relation> to b); ids are opaque and their string
                        order is not the issue order, so nothing else can decide "older"
    POST /refresh       non-interactive refresh: a new generation and a new expiry, or 409
                        {"error": "interactive"} when the identity needs interactive sign-in
    POST /admin/advance, /admin/interactive?value=0|1, /admin/lifetime?seconds=N
                        the world changing behind the port's back (test control, never called by an
                        adapter)

No real credential, profile or network beyond 127.0.0.1 is involved. The token is a stand-in
(`demo-token:<generation>:<nonce>`, the nonce fixed per issuer instance); it is what "the secret" is
in the never-capture proofs. Run as a script it prints `{"url": ...}` and serves until killed; as a
module `StubIssuer` runs in-process for the conformance suites.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

TOKEN_PREFIX = "demo-token"
DEFAULT_IDENTITY = "demo-user"
DEFAULT_LIFETIME_S = 3600.0
LOOPBACK = "127.0.0.1"


class IssuerState:
    """The issuer's world: identity, the issued generations in order, expiry, interactivity."""

    def __init__(
        self,
        identity: str = DEFAULT_IDENTITY,
        lifetime_s: float = DEFAULT_LIFETIME_S,
        interactive: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.identity = identity
        self.interactive = interactive
        self.lifetime_s = lifetime_s
        self.nonce = secrets.token_hex(8)
        self.requests: list[tuple[str, str]] = []  # (method, path) of every request served
        self._clock = clock
        self._lock = threading.Lock()
        self._issued: list[str] = []
        self._expires_at = 0.0
        self._issue()

    def _issue(self) -> str:
        n = len(self._issued) + 1
        generation = f"gen-{(n * 7919) % 100003:05d}"  # opaque: string order is not issue order
        self._issued.append(generation)
        self._expires_at = self._clock() + self.lifetime_s
        return generation

    # ---- what the routes read and change (every one under the lock)

    def host(self) -> dict[str, Any]:
        with self._lock:
            return {
                "identity": self.identity,
                "expires_at": _iso(self._expires_at),
                "generation": self._issued[-1],
                "interactive_required": self.interactive,
            }

    def current(self) -> str:
        with self._lock:
            return self._issued[-1]

    def expires_at(self) -> datetime:
        with self._lock:
            return datetime.fromtimestamp(self._expires_at, UTC)

    def token(self, generation: str | None = None) -> str:
        with self._lock:
            return f"{TOKEN_PREFIX}:{generation or self._issued[-1]}:{self.nonce}"

    def secret_values(self) -> tuple[str, ...]:
        """Everything that must never appear in a record: every token and the nonce."""
        with self._lock:
            return (self.nonce, *(f"{TOKEN_PREFIX}:{g}:{self.nonce}" for g in self._issued))

    def refresh(self) -> str | None:
        with self._lock:
            if self.interactive:
                return None
            return self._issue()

    def advance(self) -> str:
        with self._lock:
            return self._issue()

    def set_interactive(self, value: bool) -> None:
        with self._lock:
            self.interactive = value

    def set_lifetime(self, seconds: float) -> None:
        with self._lock:
            self._expires_at = self._clock() + seconds

    def order(self, a: str, b: str) -> str:
        with self._lock:
            if a not in self._issued or b not in self._issued:
                return "unknown"
            ia, ib = self._issued.index(a), self._issued.index(b)
            return "older" if ia < ib else "newer" if ia > ib else "same"

    def whoami(self, token: str) -> tuple[bool, str | None]:
        """(authenticated, the generation the token claims or None if it is not a demo token)."""
        parts = token.split(":")
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
            return False, None
        with self._lock:
            known = parts[1] in self._issued and parts[2] == self.nonce
            live = self._clock() < self._expires_at
            return (known and live), parts[1]

    def note(self, method: str, path: str) -> None:
        with self._lock:
            self.requests.append((method, path))


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _handler(state: IssuerState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib name
            return

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method: str) -> None:
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            state.note(method, url.path)
            if method == "GET" and url.path == "/host":
                self._send(200, state.host())
            elif method == "GET" and url.path == "/credential":
                self._send(200, {"token": state.token()})
            elif method == "GET" and url.path == "/whoami":
                bearer = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                ok, generation = state.whoami(bearer)
                self._send(200 if ok else 401, {"authenticated": ok, "generation": generation})
            elif method == "GET" and url.path == "/order":
                self._send(200, {"relation": state.order(query.get("a", ""), query.get("b", ""))})
            elif method == "POST" and url.path == "/refresh":
                generation = state.refresh()
                if generation is None:
                    self._send(409, {"error": "interactive"})
                else:
                    self._send(200, {"generation": generation})
            elif method == "POST" and url.path == "/admin/advance":
                self._send(200, {"generation": state.advance()})
            elif method == "POST" and url.path == "/admin/interactive":
                state.set_interactive(query.get("value", "0") == "1")
                self._send(200, {"interactive_required": state.interactive})
            elif method == "POST" and url.path == "/admin/lifetime":
                state.set_lifetime(float(query.get("seconds", "0")))
                self._send(200, state.host())
            else:
                self._send(404, {"error": "not found"})

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
            self._route("POST")

    return Handler


class StubIssuer:
    """An issuer serving on loopback in a background thread: `with StubIssuer() as issuer:`."""

    def __init__(self, **state: Any) -> None:
        self.state = IssuerState(**state)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "the issuer is not started"
        return f"http://{LOOPBACK}:{self._server.server_address[1]}"

    def start(self, port: int = 0) -> str:
        self._server = ThreadingHTTPServer((LOOPBACK, port), _handler(self.state))
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def __enter__(self) -> StubIssuer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the demo credential issuer (never real AWS)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--identity", default=DEFAULT_IDENTITY)
    parser.add_argument("--lifetime", type=float, default=DEFAULT_LIFETIME_S)
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args(argv)
    issuer = StubIssuer(
        identity=args.identity, lifetime_s=args.lifetime, interactive=args.interactive
    )
    url = issuer.start(args.port)
    print(json.dumps({"url": url}), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        issuer.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
