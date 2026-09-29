"""Lane-B facet extractors (MC-P0-02): `context_surface` and
`snapshot_manifest`, registered by `module:function` in facets.toml.

Each returns a JSON-safe value describing one public observable of today's
(S0) code; `python -m tests.pins.b_contract.facets --write` regenerates the
goldens under tests/fixtures/golden/s0/ from S0 behavior.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from trestle.common.types import PluginSnapshot
from trestle.plugin import surface
from trestle.server.snapshots import materialize_snapshot

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "golden" / "s0"
ECHO = ROOT / "tests" / "fixtures" / "plugins" / "echo.py"


def context_surface() -> dict[str, Any]:
    """The `Context` protocol's members, the bare `@trestle` decorator and
    the plugin-marker predicate."""
    methods = {
        name: str(inspect.signature(member))
        for name, member in vars(surface.Context).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }

    def _probe() -> None:
        return None

    marked = surface.trestle(_probe)
    return {
        "context": {
            "attributes": dict(surface.Context.__annotations__),
            "methods": methods,
        },
        "decorator": {
            "bare": True,
            "marker_attribute": "__trestle_plugin__",
            "returns_same_callable": marked is _probe,
            "marks_callable": surface.is_trestle_plugin(marked),
        },
        "predicate": {"unmarked_is_plugin": surface.is_trestle_plugin(lambda: None)},
    }


def snapshot_manifest() -> dict[str, Any]:
    """What materializing a declare-nothing plugin produces: the snapshot
    dataclass fields, the on-disk snapshot files, the manifest keys and the
    declare-nothing defaults."""
    sig = inspect.signature(materialize_snapshot)
    with tempfile.TemporaryDirectory(prefix="trestle-b5-facet-") as tmp:
        home = Path(tmp)
        snap = materialize_snapshot(ECHO, "echo", home=home)
        snap_dir = home / "snapshots" / snap.snapshot_id
        files = sorted(p.name for p in snap_dir.iterdir())
        manifest = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
        return {
            "snapshot_fields": [f.name for f in dataclasses.fields(PluginSnapshot)],
            "snapshot_files": files,
            "manifest_keys": sorted(manifest),
            "id_prefix": snap.snapshot_id.split("_", 1)[0] + "_",
            "declare_nothing_defaults": {
                "timeout_s": sig.parameters["timeout_s"].default,
                "summary_budget": sig.parameters["summary_budget"].default,
                "version": sig.parameters["version"].default,
            },
            "materialized_defaults": {
                "timeout_s": snap.timeout_s,
                "summary_budget": snap.summary_budget,
                "version": snap.version,
            },
        }


FACETS = {"context_surface": context_surface, "snapshot_manifest": snapshot_manifest}


def main(argv: list[str]) -> int:
    if argv != ["--write"]:
        print("usage: python -m tests.pins.b_contract.facets --write")
        return 2
    for name, func in FACETS.items():
        path = GOLDEN_DIR / f"{name}.json"
        path.write_text(json.dumps(func(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
