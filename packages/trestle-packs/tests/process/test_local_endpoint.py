"""L.RB-8.1, RACES-REPORT L-17: the local process port's endpoint contract for `PORT=0`. A bound
command that declares `PORT=0` chooses its own port and reports it (`TRESTLE_ENDPOINT_FILE`);
`create` and `restart` return only once it has, `endpoint` reads the real port, and a command that
never reports is ended and answered UNKNOWN. No test here guesses a free port."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.proof import tolerances
from tests.proof.suites.ports.families import LINEAGE, ticket
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Vantage
from trestle.workflow.values import ConfirmationStatus, OwnedHandle

from trestle_packs.process import identity, local
from trestle_packs.process.local import LocalProcessPort

from . import rig
from .test_override_app import get

EFFECT = "up"


@pytest.fixture
def port() -> Iterator[LocalProcessPort]:
    local_port = LocalProcessPort()
    try:
        yield local_port
    finally:
        local_port.close()


def spec_over(tmp_path: Path) -> ports.ResourceSpec:
    env = {"PORT": "0", "APP_EVENT_LOG": str(tmp_path / "events.log")}
    return rig.spec_for(sys.executable, str(rig.APP), env)


def reached(local_port: LocalProcessPort, selector: str) -> int:
    """The port `endpoint` reads for the owned process at `selector`, asked and answered."""
    handle = OwnedHandle(LINEAGE, EFFECT, selector, ports.InRunGroup())
    where = local_port.endpoint(handle, Vantage.HOST)
    assert isinstance(where, ports.Endpoint), where
    assert where.host == "127.0.0.1" and where.port > 0
    return int(where.port)


def pid_at(where: int) -> int:
    status, body = get(where, "/")
    assert status == 200
    return int(json.loads(body)["pid"])


def test_create_with_port_zero_yields_an_endpoint_the_test_can_get(
    tmp_path: Path, port: LocalProcessPort
) -> None:
    made = port.create(spec_over(tmp_path), ticket(EFFECT, EffectFacetClass.CREATE, Lifetime.RUN))
    assert made.status is ConfirmationStatus.APPLIED and made.identity
    where = reached(port, made.identity)
    assert get(where, "/health") == (200, b"ok")  # create returned only once it listened
    assert pid_at(where) == port._instances[made.identity].proc.pid  # noqa: SLF001


def test_a_command_that_never_reports_is_ended_and_unknown(
    tmp_path: Path, port: LocalProcessPort, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(local, "LISTEN_WAIT_S", tolerances.POLL_FINE_S * 4)
    mute = rig.spec_for(
        sys.executable, "-c", {"PORT": "0"}, "import time; time.sleep(30)"
    )  # a command line that is Python's own: it never writes the endpoint file
    pids: list[int] = []
    real_popen = local.subprocess.Popen

    class Recording(real_popen):  # type: ignore[type-arg, misc]
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[call-overload]
            pids.append(self.pid)

    monkeypatch.setattr(local.subprocess, "Popen", Recording)
    made = port.create(mute, ticket(EFFECT, EffectFacetClass.CREATE, Lifetime.RUN))
    assert made.status is ConfirmationStatus.UNKNOWN  # it started, so never NOT_APPLIED
    assert len(pids) == 1 and not port._instances  # noqa: SLF001
    assert identity.start_time(pids[0]) is None, "the silent process is ended and reaped"
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_restart_yields_a_fresh_endpoint(tmp_path: Path, port: LocalProcessPort) -> None:
    made = port.create(spec_over(tmp_path), ticket(EFFECT, EffectFacetClass.CREATE, Lifetime.RUN))
    assert made.status is ConfirmationStatus.APPLIED and made.identity
    handle = OwnedHandle(LINEAGE, EFFECT, made.identity, ports.InRunGroup())
    before = pid_at(reached(port, made.identity))
    again = port.restart(handle, ticket("restart", EffectFacetClass.OWNED, Lifetime.RUN))
    assert again.status is ConfirmationStatus.APPLIED
    where = reached(port, made.identity)  # the relaunch returned only once it had reported
    assert get(where, "/health") == (200, b"ok")
    assert pid_at(where) != before  # a new process, on the address it reported
