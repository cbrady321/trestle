#!/usr/bin/env python3
"""Phase E smoke — operator API golden path (matches trestle ops serve wiring)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
PLUGIN_SRC = REPO / "examples" / "plugins"
FIXTURE_SRC = REPO / "tests" / "fixtures" / "plugins"
WEB_DIST = REPO / "console" / "web" / "dist"


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="trestle-ops-smoke-"))
    plugins = home / "plugins"
    plugins.mkdir()
    for src_dir in (PLUGIN_SRC, FIXTURE_SRC):
        for path in src_dir.glob("*.py"):
            if not (plugins / path.name).exists():
                shutil.copy(path, plugins / path.name)

    from trestle.ops.gateway import create_app
    from trestle.server.main import create_kernel

    kernel = create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)
    client = TestClient(create_app(kernel))

    health = client.get("/ops/v1/health")
    assert health.status_code == 200, health.text
    health_body = health.json()
    assert health_body["issued"] is True
    assert health_body["body"]["reachable"] is True
    assert health_body["body"]["plugin_count"] >= 1

    run = kernel.control.run(plugin="echo", args={"message": "ops-smoke"}, wait_ms=5000)
    assert run.state == "succeeded", run.state
    run_id = run.run_id

    sessions = client.post("/ops/v1/sessions/recent_runs/rows", json={"params": {}})
    assert sessions.status_code == 200, sessions.text
    sessions_body = sessions.json()
    assert sessions_body["issued"] is True
    assert any(row.get("run_id") == run_id for row in sessions_body["body"]["items"])

    chunk = client.post(
        "/ops/v1/telemetry/chunk",
        json={
            "handle": f"{run_id}/result",
            "window": {"kind": "jsonpath", "expr": "$.message"},
        },
    )
    assert chunk.status_code == 200, chunk.text
    chunk_body = chunk.json()
    assert chunk_body["issued"] is True
    assert chunk_body["body"]["values"] == ["ops-smoke"]

    refusal = client.post("/ops/v1/sessions/plugin_stats/rows", json={"params": {}})
    assert refusal.status_code == 200, refusal.text
    refusal_body = refusal.json()
    assert refusal_body["issued"] is False
    assert refusal_body["body"]["origin"] == "projection"
    assert "run_id" not in refusal_body["body"]

    if WEB_DIST.is_dir():
        ui = client.get("/")
        assert ui.status_code == 200, ui.text
        assert "text/html" in ui.headers.get("content-type", "")
    else:
        print("WARN: console/web/dist missing — build with: cd console/web && npm install && npm run build")

    print(f"SMOKE OK run_id={run_id} fetch={chunk_body['body'].get('values')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
