"""Operator API tests — admission-identical ControlSurface projection."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from trestle.ops.gateway import create_app
from trestle.server.main import create_kernel

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def ops_client(tmp_path: Path) -> TestClient:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    for src in (REPO / "examples" / "plugins", REPO / "tests" / "fixtures" / "plugins"):
        for path in src.glob("*.py"):
            if not (plugins / path.name).exists():
                shutil.copy(path, plugins / path.name)
    kernel = create_kernel(home=tmp_path)
    return TestClient(create_app(kernel))


def test_ops_health(ops_client: TestClient) -> None:
    resp = ops_client.get("/ops/v1/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["issued"] is True
    assert payload["body"]["reachable"] is True
    assert payload["body"]["registry_version"] >= 1


def test_ops_session_rows_recent_runs(ops_client: TestClient) -> None:
    kernel = ops_client.app.state.kernel
    view = kernel.control.run(plugin="echo", args={"message": "ops"}, wait_ms=5000)
    assert view.state == "succeeded"

    resp = ops_client.post("/ops/v1/sessions/recent_runs/rows", json={"params": {}})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["issued"] is True
    assert any(row.get("run_id") == view.run_id for row in payload["body"]["items"])


def test_ops_telemetry_chunk_result(ops_client: TestClient) -> None:
    kernel = ops_client.app.state.kernel
    view = kernel.control.run(plugin="echo", args={"message": "chunk"}, wait_ms=5000)
    assert view.run_id

    resp = ops_client.post(
        "/ops/v1/telemetry/chunk",
        json={
            "handle": f"{view.run_id}/result",
            "window": {"kind": "jsonpath", "expr": "$.message"},
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["issued"] is True
    assert payload["body"]["values"] == ["chunk"]


def test_ops_invalid_view_refusal(ops_client: TestClient) -> None:
    resp = ops_client.post("/ops/v1/sessions/plugin_stats/rows", json={"params": {}})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["issued"] is False
    assert payload["body"]["origin"] == "projection"
    assert "run_id" not in payload["body"]
