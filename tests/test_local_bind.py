"""Local-only HTTP bind enforcement."""

from __future__ import annotations

import inspect

import pytest

from trestle.cli import main
from trestle.common.bind import LOOPBACK_HOST
from trestle.ops.serve import run_ops_server
from trestle.server.main import run_server


def test_cli_serve_rejects_host_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--host", "0.0.0.0"])
    assert exc.value.code != 0


def test_cli_ops_serve_rejects_host_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ops", "serve", "--host", "0.0.0.0"])
    assert exc.value.code != 0


def test_run_server_uses_loopback() -> None:
    params = inspect.signature(run_server).parameters
    assert "host" not in params
    source = inspect.getsource(run_server)
    assert "LOOPBACK_HOST" in source


def test_run_ops_server_uses_loopback() -> None:
    params = inspect.signature(run_ops_server).parameters
    assert "host" not in params
    source = inspect.getsource(run_ops_server)
    assert "LOOPBACK_HOST" in source


def test_loopback_host_constant() -> None:
    assert LOOPBACK_HOST == "127.0.0.1"
