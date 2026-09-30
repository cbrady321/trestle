"""The local stdlib HTTP app rig the PROC cases share (L.RB-2.1, L.RB-5.2; not a test module).

`APP` is `tests/fixtures/apps/http_app.py`; `free_port` a loopback port; `AwaitListening` the create
facet of the local process port returning once the app says it is listening (the loop's clock is
manual in these cases, so process start-up is the harness's to wait for; the readiness contract is
the read facet's)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tests.proof import tolerances
from trestle.workflow import ports
from trestle_packs.process.local import LocalProcessPort

APP = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "apps" / "http_app.py"
REFUSED = 3  # the app answers `/health` 503 this many times before the first 200


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class AwaitListening:
    """The create facet of the local port, returning once the app says it is listening.

    The loop's clock is manual here (no test sleeps), so without this a poll could ask a
    still-starting app: process start-up is the harness's to wait for, the readiness contract is
    the read facet's. Every other member is the port's own."""

    def __init__(self, inner: LocalProcessPort, log: Path) -> None:
        self._inner = inner
        self._log = log

    def create(self, spec: ports.ResourceSpec, ticket: Any) -> Any:
        confirmation = self._inner.create(spec, ticket)
        deadline = time.monotonic() + tolerances.JOIN_WAIT_S
        while time.monotonic() < deadline:
            if self._log.exists() and "listening" in self._log.read_text().splitlines():
                return confirmation
            time.sleep(tolerances.POLL_FINE_S)
        raise AssertionError("the app never said it was listening")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
