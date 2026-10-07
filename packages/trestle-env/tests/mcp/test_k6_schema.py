"""No entry-point schema lets an agent reach the volume reset (L.RB-3.4; K-6, WR-OWN-4).

`reset_volumes=True` is a Python-only argument of `StackRunner.down` (docs/packs.md, Teardown).
Every plugin a kernel registers from the shipped plugin directories (the legacy packs in
examples/packs and the reference plugin) is described the way an agent reads it,
`describe_plugin`, and no published input schema carries a field that names a reset, at any depth.

The agent-published reset itself is not decided: `WR-OWN-4:agent-published-reset` is declared
`posture = "gated_on"`, `oq = "F-13(d)"` and listed by `python -m tests.proof.meta
open-questions`; no node claims it."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from trestle.server.main import Kernel, create_kernel

from trestle_env.plugins import reference_env

REPO = Path(__file__).resolve().parents[4]
LEGACY = REPO / "examples" / "packs"
REFERENCE = Path(reference_env.__file__).resolve().parent


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    legacy = tmp_path / "packs"
    legacy.mkdir()
    for path in LEGACY.glob("*.py"):
        shutil.copy(path, legacy / path.name)
    built = create_kernel(
        home=tmp_path / "home", plugin_dirs=[legacy, REFERENCE], skip_recovery=True
    )
    built.registry.refresh()
    return built


def reset_fields(node: Any, where: str = "") -> list[str]:
    """Every property (or `$defs` entry) whose name mentions a reset, with its path."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, child in node.items():
            here = f"{where}.{key}" if where else key
            if key in ("properties", "$defs", "definitions") and isinstance(child, dict):
                found += [f"{here}.{name}" for name in child if "reset" in name.lower()]
            found += reset_fields(child, here)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found += reset_fields(child, f"{where}[{index}]")
    return found


@pytest.mark.proves("WR-OWN-4", "WR-OWN-4:reset-not-agent-reachable", "B", "B", "MCP", "CI")
def test_no_entry_point_schema_exposes_reset_volumes(kernel: Kernel) -> None:
    names = sorted(row["name"] for row in kernel.registry.catalog().to_dict()["items"])
    # the teardown-selecting legacy entry points and the reference plugin are all registered
    assert {"docker_stack", "integration_pipeline", "reference_env"} <= set(names), names
    for name in names:
        described = kernel.control.describe_plugin(name)
        assert isinstance(described, dict), (name, described)
        schema = described["input_schema"]
        assert schema.get("properties"), (name, schema)  # a real schema was published
        assert reset_fields(schema) == [], name
        assert "reset" not in str(schema).lower(), name
