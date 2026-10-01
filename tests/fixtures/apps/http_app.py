#!/usr/bin/env python3
"""A stdlib HTTP app with a declared readiness endpoint (L.RB-2.1; hld-wr-environment KDD 2).

The reference tree's HTTP readiness contract is `GET /health` answers 200 with exactly `ok`
(`trestle_env.tree.HTTP_SUPPORT_READINESS`). This app serves it in one of two modes, named by its
first command-line argument:

* `ready-after <n>`: the first `n` requests for `/health` are answered 503 (`not ready`), every
  later one 200 `ok`, so a poller sees `n` refusals before the pass;
* `never`: `/health` is always 503; the service listens but never becomes ready (L.RB-2.3).

`PORT` (environment, required) is the loopback port it listens on. `APP_EVENT_LOG` (environment,
optional) is a file it appends `listening` to once it accepts connections, `health <status>` after
each `/health` request and `stop` when told to end (SIGTERM or SIGINT), so a test reads the order
of what the app saw from the app itself. Any other path is 404. Imports only the standard library;
logs nothing to the console.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

READY_BODY = b"ok"
NOT_READY_BODY = b"not ready"


def note(event: str) -> None:
    path = os.environ.get("APP_EVENT_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as sink:
            sink.write(event + "\n")


class Readiness:
    """How many `/health` requests are refused before the first pass (`None`: never ready)."""

    def __init__(self, refuse: int | None) -> None:
        self._refuse = refuse
        self._seen = 0
        self._lock = threading.Lock()

    def status(self) -> int:
        with self._lock:
            self._seen += 1
            if self._refuse is not None and self._seen > self._refuse:
                return 200
            return 503


def parse(argv: list[str]) -> Readiness:
    mode = argv[0] if argv else ""
    if mode == "never":
        return Readiness(None)
    if mode == "ready-after" and len(argv) > 1 and argv[1].isdigit():
        return Readiness(int(argv[1]))
    raise SystemExit("usage: http_app.py never | ready-after <n>")


def handler_for(readiness: Readiness) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            if self.path == "/health":
                status = readiness.status()
                body = READY_BODY if status == 200 else NOT_READY_BODY
                note(f"health {status}")
            else:
                status, body = 404, b"not found"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the stdlib's name
            return None

    return Handler


def main() -> int:
    readiness = parse(sys.argv[1:])
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), handler_for(readiness))

    def end(signum: int, frame: object) -> None:
        raise SystemExit(0)  # unwinds serve_forever into the `finally` below

    signal.signal(signal.SIGTERM, end)
    signal.signal(signal.SIGINT, end)
    note("listening")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        note("stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
