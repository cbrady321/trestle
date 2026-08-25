"""Operator API envelope and route wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from trestle.common.types import RequestOutcome
from trestle.ops.health import read_health
from trestle.ops.sessions import read_session_rows
from trestle.ops.telemetry import read_telemetry_chunk
from trestle.server.main import Kernel


def _envelope(body: dict[str, Any], *, issued: bool = True, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"issued": issued, "body": body}, status_code=status_code)


def _wire_control_result(value: RequestOutcome | dict[str, Any]) -> JSONResponse:
    if isinstance(value, RequestOutcome):
        return _envelope(value.to_dict(), issued=False)
    return _envelope(value)


def create_app(kernel: Kernel) -> Starlette:
    async def health(request: Request) -> JSONResponse:
        try:
            return _wire_control_result(read_health(kernel))
        except Exception as exc:  # noqa: BLE001 — operator boundary
            return _envelope(
                {
                    "code": "operator.unreachable",
                    "message": str(exc)[:200],
                    "retryable": True,
                    "origin": "operator",
                },
                issued=False,
                status_code=502,
            )

    async def session_rows(request: Request) -> JSONResponse:
        view = request.path_params["view"]
        payload = await request.json()
        params = payload.get("params") or {}
        cursor = payload.get("cursor")
        return _wire_control_result(
            read_session_rows(kernel, view=view, params=params, cursor=cursor)
        )

    async def telemetry_chunk(request: Request) -> JSONResponse:
        payload = await request.json()
        handle = str(payload.get("handle", ""))
        window = payload.get("window") or {}
        return _wire_control_result(read_telemetry_chunk(kernel, handle=handle, window=window))

    routes: list[Route | Mount] = [
        Route("/ops/v1/health", health, methods=["GET"]),
        Route("/ops/v1/sessions/{view}/rows", session_rows, methods=["POST"]),
        Route("/ops/v1/telemetry/chunk", telemetry_chunk, methods=["POST"]),
    ]

    web_dist = Path(__file__).resolve().parents[2] / "console" / "web" / "dist"
    if web_dist.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=web_dist, html=True), name="web"))

    app = Starlette(routes=routes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.kernel = kernel
    return app
