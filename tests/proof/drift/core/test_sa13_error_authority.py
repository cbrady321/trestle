"""SA-13 in core (L.CS-3.2, L.CS-3.3): a run's error has one authority, the ledger's `error_record`
row (MC-15). Every projection of it (`meta.json.error`, the run view's `error` and the
`last_error` view's message, the last two read through the MCP host) equals the row's
{code, phase, message}, before and after the whole server is killed and restarted on the same home,
and the row's code is in the execution vocabulary (MC-CORE-04) or the admission codes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.core.spine.plugins.raiser import SENTINEL
from tests.proof import mcp_host, records, tolerances
from trestle.common import codes

FIELDS = ("code", "phase", "message")


def _admission_codes() -> set[str]:
    return {
        value
        for name, value in vars(codes).items()
        if isinstance(value, str) and value.startswith("admission.")
    }


def _ledger_row(run_dir: Path) -> dict[str, Any]:
    rows = [r for r in records.ledger_rows(run_dir).rows if r["kind"] == "error_record"]
    assert len(rows) == 1, rows
    return {key: rows[0][key] for key in FIELDS}


def _projections(host: mcp_host.McpHost, run_dir: Path) -> dict[str, Any]:
    """Every place the run's error is read from, by name, each reduced to {code, phase, message}
    (`last_error` carries only the message, so it is compared on that field)."""
    meta = json.loads((run_dir / "evidence" / "meta.json").read_text(encoding="utf-8"))
    answer = host.call(
        "await_runs",
        {"run_ids": [run_dir.name], "mode": "all", "timeout_ms": tolerances.HARNESS_WAIT_MS},
    )
    views = answer["result"] if isinstance(answer, dict) and "result" in answer else answer
    (view,) = views
    last = host.call("query", {"view": "last_error", "params": {"run_id": run_dir.name}})
    (last_row,) = last["items"]
    return {
        "meta.json.error": meta.get("error"),
        "RunView.error": view.get("error"),
        "last_error.message": last_row["message"],
    }


def _assert_all_equal_ledger_row(host: mcp_host.McpHost, run_dir: Path) -> dict[str, Any]:
    row = _ledger_row(run_dir)
    assert row["code"] in codes.EXECUTION_CODES | _admission_codes()
    assert SENTINEL in row["message"]
    for name, projected in _projections(host, run_dir).items():
        expected = row["message"] if name == "last_error.message" else row
        assert projected == expected, f"{name} differs from the ledger row"
    return row


@pytest.mark.parametrize("sa", ["SA-13"])
def test_all_projections_equal_ledger_row(sa: str, tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        shutil.copy(support.SPINE_PLUGIN_DIR / "raiser.py", host.home / "plugins")
        view = host.call("run", {"plugin": "raiser", "wait_ms": tolerances.HARNESS_WAIT_MS})
        assert view["state"] == "failed", view
        (run_dir,) = sorted((host.home / "runs").glob(f"*/{view['run_id']}"))
        before = _assert_all_equal_ledger_row(host, run_dir)

        host.kill_server()
        host.restart()
        after = _assert_all_equal_ledger_row(host, run_dir)
        assert after == before  # the restart neither adds a row nor changes the one there
