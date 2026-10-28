"""L.RB-8.1: the stdlib override app fixture (`tests/fixtures/apps/override_app.py`) answers on the
port it chose (`PORT=0`, reported through `TRESTLE_ENDPOINT_FILE`), says `start` and `stop` in its
event log, and ends on SIGTERM."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from tests.proof import tolerances

from . import rig


def get(port: int, path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def reported(endpoint: Path) -> int:
    """The port the app reported in its endpoint file (`127.0.0.1:<port>`)."""
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while time.monotonic() < deadline:
        if endpoint.exists():
            return int(endpoint.read_text().rsplit(":", 1)[1])
        time.sleep(tolerances.POLL_FINE_S)
    raise AssertionError("the override app never reported its port")


def test_the_override_app_answers_health_and_root_and_ends_on_sigterm(tmp_path: Path) -> None:
    log, endpoint = tmp_path / "events.log", tmp_path / "app.endpoint"
    app = subprocess.Popen(  # noqa: S603 - the fixture under test
        [sys.executable, str(rig.APP)],
        env={
            "PORT": "0",
            "TRESTLE_ENDPOINT_FILE": str(endpoint),
            "APP_EVENT_LOG": str(log),
            "PATH": "/usr/bin:/bin",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        port = reported(endpoint)
        assert get(port, "/health") == (200, b"ok")
        status, body = get(port, "/")
        assert status == 200 and json.loads(body) == {"app": "override", "pid": app.pid}
        assert get(port, "/nope")[0] == 404
        app.terminate()
        assert app.wait(timeout=tolerances.JOIN_WAIT_S) == 0
    finally:
        if app.poll() is None:
            app.kill()
            app.wait()
    assert log.read_text().split() == ["start", "stop"]
    assert app.stdout is not None and app.stdout.read() == b""  # it logs nothing to the console
    with pytest.raises(OSError):
        get(port, "/health")  # and the port is free again


def test_the_override_app_needs_a_port(tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603 - the fixture under test
        [sys.executable, str(rig.APP)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S,
        check=False,
    )
    assert done.returncode != 0 and b"PORT" in done.stderr
