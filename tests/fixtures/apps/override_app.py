#!/usr/bin/env python3
"""A stdlib HTTP app: the agent-launched Python project of the override realization (L.RB-8.1;
B3-C4, B3-C21, hld-wr-environment "Gradle or Python, through the toolchain port").

`PORT` (environment, required) is the loopback port it listens on (`0`: a free one, reported through
`TRESTLE_ENDPOINT_FILE`); `GET /health` answers 200 `ok`
and `GET /` answers 200 with `{"app": "override", "pid": <pid>}` (a restart is visible as a new
pid); anything else is 404. `APP_EVENT_LOG` (environment, optional) is a file it appends `start`
to once it listens and `stop` to when it is told to end (SIGTERM or SIGINT), so a test can read the
order of a restart's stop and start from the app itself. Imports only the standard library; logs
nothing to the console. Extra command-line arguments are ignored (a test names its instance in
them, so its command line is its own).
"""

from __future__ import annotations

import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def note(event: str) -> None:
    path = os.environ.get("APP_EVENT_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as sink:
            sink.write(event + "\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        if self.path == "/health":
            body, status = b"ok", 200
        elif self.path == "/":
            body, status = json.dumps({"app": "override", "pid": os.getpid()}).encode(), 200
        else:
            body, status = b"not found", 404
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the stdlib's name
        return None


def report_endpoint(server: ThreadingHTTPServer) -> None:
    """With `PORT=0` the app picked its own port: it writes its bound address to the file
    `TRESTLE_ENDPOINT_FILE` names, atomically (the local process port's endpoint contract)."""
    target = os.environ.get("TRESTLE_ENDPOINT_FILE")
    if os.environ.get("PORT") != "0" or not target:
        return
    partial = target + ".partial"
    with open(partial, "w", encoding="utf-8") as sink:
        sink.write(f"127.0.0.1:{server.server_address[1]}")
    os.replace(partial, target)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler)

    def end(signum: int, frame: object) -> None:
        raise SystemExit(0)  # unwinds serve_forever into the `finally` below

    signal.signal(signal.SIGTERM, end)
    signal.signal(signal.SIGINT, end)
    report_endpoint(server)
    note("start")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        note("stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
