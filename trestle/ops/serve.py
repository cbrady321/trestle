"""Run operator HTTP server."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from trestle.common.bind import LOOPBACK_HOST
from trestle.ops.gateway import create_app
from trestle.server.main import create_kernel


def run_ops_server(
    *,
    port: int = 18733,
    home: Path | None = None,
) -> int:
    kernel = create_kernel(home=home)
    app = create_app(kernel)
    uvicorn.run(app, host=LOOPBACK_HOST, port=port, log_level="warning")
    return 0
