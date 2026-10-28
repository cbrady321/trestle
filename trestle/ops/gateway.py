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
from trestle.ops.actions import (
    cancel_run,
    join_waits,
    join_waits_to_dict,
    pin_retention,
    unpin_retention,
)
from trestle.ops.health import read_health
from trestle.ops.host_wiring import read_host_wiring
from trestle.ops.registry import describe_registry_entry, iter_registry
from trestle.ops.sessions import read_session_rows
from trestle.ops.telemetry import read_telemetry_chunk
from trestle.server.main import Kernel


def _envelope(body: dict[str, Any], *, issued: bool = True, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"issued": issued, "body": body}, status_code=status_code)


def _wire_control_result(value: RequestOutcome | dict[str, Any]) -> JSONResponse:
    if isinstance(value, RequestOutcome):
        return _envelope(value.to_dict(), issued=False)
    return _envelope(value)


def _wire_action_result(value: RequestOutcome) -> JSONResponse:
    body = value.to_dict()
    issued = value.code.endswith("_accepted")
    return _envelope(body, issued=issued)


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

    async def registry_list(request: Request) -> JSONResponse:
        return _wire_control_result(iter_registry(kernel))

    async def registry_describe(request: Request) -> JSONResponse:
        plugin_id = request.path_params["plugin_id"]
        return _wire_control_result(describe_registry_entry(kernel, plugin_id))

    async def host_wiring(request: Request) -> JSONResponse:
        return _envelope(read_host_wiring())

    async def action_cancel(request: Request) -> JSONResponse:
        payload = await request.json()
        handle = str(payload.get("handle", ""))
        return _wire_action_result(cancel_run(kernel, handle))

    async def action_pin(request: Request) -> JSONResponse:
        handle = request.path_params["handle"]
        return _wire_action_result(pin_retention(kernel, handle))

    async def action_unpin(request: Request) -> JSONResponse:
        handle = request.path_params["handle"]
        return _wire_action_result(unpin_retention(kernel, handle))

    async def waits_join(request: Request) -> JSONResponse:
        payload = await request.json()
        handles = list(payload.get("handles") or [])
        mode = str(payload.get("mode", "all"))
        timeout_ms = int(payload.get("timeout_ms", 2000))
        return _wire_control_result(
            join_waits_to_dict(join_waits(kernel, handles, mode=mode, timeout_ms=timeout_ms))
        )

    routes: list[Route | Mount] = [
        Route("/ops/v1/health", health, methods=["GET"]),
        Route("/ops/v1/host_wiring", host_wiring, methods=["GET"]),
        Route("/ops/v1/registry", registry_list, methods=["GET"]),
        Route("/ops/v1/registry/{plugin_id}", registry_describe, methods=["GET"]),
        Route("/ops/v1/sessions/{view}/rows", session_rows, methods=["POST"]),
        Route("/ops/v1/telemetry/chunk", telemetry_chunk, methods=["POST"]),
        Route("/ops/v1/actions/cancel", action_cancel, methods=["POST"]),
        Route("/ops/v1/retention/{handle}", action_pin, methods=["PUT"]),
        Route("/ops/v1/retention/{handle}", action_unpin, methods=["DELETE"]),
        Route("/ops/v1/waits/join", waits_join, methods=["POST"]),
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
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    app.state.kernel = kernel
    return app
