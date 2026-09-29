"""Lane C golden-facet extractors (L.P0-1C.5; MC-P0-02).

Each function returns one JSON-safe value describing a public observable of
today's (S0) host, registered in `tests/proof/facets.toml` as
`tests.pins.c_surface.facets:<function>`:

- `tools_list`     raw `tools/list` over the stdio MCP host (SA-12)
- `runview_shape`  the `RunView` frame: fields, and key/type shape per state
- `spec_keys`      the run spec: `RunSpec` fields and the keys of a written spec.json
- `refusal_codes`  every stable refusal code plus the refusal frame shape

Values that vary run to run (ids, durations, hashes, timestamps) are never
extracted, only their keys and type names. `python -m tests.pins.c_surface.facets
--write` regenerates the goldens under `tests/fixtures/golden/s0/`.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from tests.proof import harness, normalize, tolerances
from tests.proof.mcp_host import McpHost
from trestle.common import codes
from trestle.common.types import RequestOutcome, RunSpec, RunView

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "golden" / "s0"
FACET_FUNCTIONS = ("tools_list", "runview_shape", "spec_keys", "refusal_codes")
BIG_ARRAY_COUNT = 10_000  # over the default 4096 B summary budget


def _type_names(frame: dict[str, Any]) -> dict[str, str]:
    return {key: type(frame[key]).__name__ for key in sorted(frame)}


def _fields(cls: type) -> list[list[str]]:
    return [[f.name, str(f.type)] for f in dataclasses.fields(cls)]


def tools_list() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="trestle-c-tools-") as tmp:
        host = McpHost(home=Path(tmp) / "home")
        try:
            raw = host.tools_list_raw()
        finally:
            host.close()
    message = json.loads(raw)
    result = message["result"]
    tools = result["tools"]
    return {
        "result_keys": sorted(result),
        "meta_keys": sorted(result.get("_meta") or {}),
        "tool_names": [tool["name"] for tool in tools],
        "tools_bytes": len(json.dumps(tools, separators=(",", ":")).encode("utf-8")),
        "tools": tools,
    }


def _run_views(home: Path) -> dict[str, dict[str, Any]]:
    kernel = harness.fresh_kernel(home=home)
    wait_ms = tolerances.HARNESS_WAIT_MS
    views: dict[str, RunView] = {}
    for label, plugin, args in (
        ("succeeded", "echo", {"message": "shape"}),
        ("truncated_array", "big_array", {"count": BIG_ARRAY_COUNT}),
        ("truncated_object", "hostile", {"flood_lines": 0, "flood_events": 0}),
    ):
        result = kernel.control.run(plugin=plugin, args=args, wait_ms=wait_ms)
        assert isinstance(result, RunView), result
        views[label] = result
    return {label: view.to_dict() for label, view in views.items()}


def runview_shape() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="trestle-c-view-") as tmp:
        frames = _run_views(Path(tmp) / "home")
    return {
        "fields": _fields(RunView),
        "status_frame_version": frames["succeeded"]["status_frame_version"],
        "frames": {label: _type_names(frame) for label, frame in frames.items()},
    }


def spec_keys() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="trestle-c-spec-") as tmp:
        home = Path(tmp) / "home"
        kernel = harness.fresh_kernel(home=home)
        run_dir = harness.run_to_dir(kernel, "echo", {"message": "spec"})
        spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    return {
        "runspec_fields": _fields(RunSpec),
        "spec_json_keys": sorted(spec),
        "spec_json_types": _type_names(spec),
    }


def refusal_codes() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="trestle-c-refusal-") as tmp:
        kernel = harness.fresh_kernel(home=Path(tmp) / "home")
        refusal = kernel.control.run(plugin="no_such_plugin", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(refusal, RequestOutcome), refusal
    constants = {
        name: value
        for name, value in sorted(vars(codes).items())
        if name.isupper() and isinstance(value, str)
    }
    return {
        "codes": constants,
        "outcome_fields": _fields(RequestOutcome),
        "refusal_frame": _type_names(refusal.to_dict()),
        "refusal_code": refusal.code,
    }


def render(facet: str) -> str:
    value = normalize.normalize(globals()[facet]())
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["--write"]:
        print("usage: python -m tests.pins.c_surface.facets --write", file=sys.stderr)
        return 2
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for facet in FACET_FUNCTIONS:
        (GOLDEN_DIR / f"{facet}.json").write_text(render(facet), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
