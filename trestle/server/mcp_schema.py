"""Small MCP inputSchema annotations for the ten-tool MCP surface (G1).

Runtime types stay open (`str` / `dict`) so unknown views and window kinds still
reach Kernel refusals (`projection.invalid_view` / `projection.invalid_args`)
instead of protocol validation errors. Enums are published for discovery only.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from trestle.query.views import FETCH_WINDOW_KINDS, VIEW_NAME_VALUES

QueryViewArg = Annotated[
    str,
    Field(json_schema_extra={"enum": list(VIEW_NAME_VALUES)}),
]

FetchWindowArg = Annotated[
    dict[str, Any],
    Field(
        json_schema_extra={
            "type": "object",
            "additionalProperties": True,
            "required": ["kind"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(FETCH_WINDOW_KINDS),
                }
            },
        }
    ),
]
