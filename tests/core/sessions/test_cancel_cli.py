"""Change 2a: `trestle cancel <run_id>`, the operator's unscoped cancel (exit 0 accepted, 1 refused,
2 a refused home), against a run started over MCP and against runs admitted in-process."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.trees import generators
from tests.proof import harness, mcp_host, tolerances
from trestle import cli
from trestle.server import answer

WAIT_MS = int(tolerances.JOIN_WAIT_S * 3 * 1000)


def _cancel(capsys: pytest.CaptureFixture[str], run_id: str, home: Path) -> tuple[int, str, str]:
    code = cli.main(["cancel", run_id, "--home", str(home)])
    out, err = capsys.readouterr()
    return code, out, err


def test_cancel_a_run_started_over_mcp(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with mcp_host.McpHost(home=tmp_path / "home") as host:
        frame = host.call("run", {"plugin": "slow", "args": {"seconds": 60.0}, "wait_ms": 0})
        run_id = frame["run_id"]
        code, out, err = _cancel(capsys, run_id, host.home)
        assert code == 0, err
        assert out.splitlines() == [f"cancel accepted for {run_id}"]
        (view,) = host.call("await_runs", {"run_ids": [run_id], "timeout_ms": WAIT_MS})["result"]
        assert view["state"] == "cancelled", view
        code, out, err = _cancel(capsys, run_id, host.home)
        assert code == 1 and out == ""
        assert err.startswith("trestle cancel: ") and "not cancellable" in err
        code, out, err = _cancel(capsys, "r_nosuchrun0000000000", host.home)
        assert code == 1 and "unknown run" in err


def _admitted_tree(tmp_path: Path) -> harness.AdmittedTree:
    """A three-level tree admitted in-process (queued, never driven): a root with child handles."""
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    kernel = harness.fresh_kernel([plugins], home=tmp_path / "home")
    plugin = generators.fixture_tree("three_level").write(plugins)
    return harness.admit_tree(plugin, kernel=kernel)


def test_cancel_a_child_handle_names_the_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _admitted_tree(tmp_path)
    handle = answer.child_handle(run.run_id, ("data", "db"))
    code, out, err = _cancel(capsys, handle, run.kernel.home)
    assert code == 1 and out == ""
    assert run.run_id in err and "root" in err


def test_cancel_when_the_owner_is_gone_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _admitted_tree(tmp_path)
    kernel = run.kernel
    assert kernel.ownership is not None
    kernel.ownership.drop_all()  # its server died
    code, out, err = _cancel(capsys, run.run_id, kernel.home)
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == f"cancel accepted for {run.run_id}"
    assert lines[1].startswith("its server is not running: the run ends interrupted")


def test_a_refused_home_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "format").write_text("99\n")
    code, _, err = _cancel(capsys, "r_x", home)
    assert code == 2 and err.startswith("trestle: ")
