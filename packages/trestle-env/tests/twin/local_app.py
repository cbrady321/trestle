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
    """The create facet of the local port, returning once the app says it is listening; its
    `restart` (the owned facet's relaunch) returns once the NEW process says so.

    The loop's clock is manual here (no test sleeps), so without this a poll could ask a
    still-starting app: process start-up is the harness's to wait for, the readiness contract is
    the read facet's. A relaunch is a start-up too: unwaited, the remedy's whole wait can run out
    on the manual clock before the new interpreter listens (seen in a loaded nested CI run).
    Every other member is the port's own."""

    def __init__(self, inner: LocalProcessPort, log: Path) -> None:
        self._inner = inner
        self._log = log

    def _listened(self) -> int:
        if not self._log.exists():
            return 0
        return self._log.read_text().splitlines().count("listening")

    def _until_listened(self, more_than: int) -> None:
        deadline = time.monotonic() + tolerances.JOIN_WAIT_S
        while time.monotonic() < deadline:
            if self._listened() > more_than:
                return
            time.sleep(tolerances.POLL_FINE_S)
        raise AssertionError("the app never said it was listening")

    def create(self, spec: ports.ResourceSpec, ticket: Any) -> Any:
        confirmation = self._inner.create(spec, ticket)
        self._until_listened(0)
        return confirmation

    def restart(self, target: Any, ticket: Any) -> Any:
        before = self._listened()
        confirmation = self._inner.restart(target, ticket)
        self._until_listened(before)
        return confirmation

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
