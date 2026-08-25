"""Run operator HTTP server."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from trestle.ops.gateway import create_app
from trestle.server.main import create_kernel


def run_ops_server(
    *,
    host: str = "127.0.0.1",
    port: int = 18733,
    home: Path | None = None,
) -> int:
    kernel = create_kernel(home=home)
    app = create_app(kernel)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
