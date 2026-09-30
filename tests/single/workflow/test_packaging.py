"""L.SL-3.1 (BFD-47): `trestle-packs` depends on `trestle`, so a pack may import the workflow
package's port types (N7). The fakes stay stdlib-only; this only opens the import for the real
adapters that follow (L.SL-3.2, L.SL-3.3)."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKS = ROOT / "packages" / "trestle-packs"


def _requirement_name(requirement: str) -> str:
    for stop in "[<>=!~;@ ":
        requirement = requirement.split(stop, 1)[0]
    return requirement.strip().lower().replace("_", "-")


def test_trestle_packs_declares_trestle() -> None:
    project = tomllib.loads((PACKS / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "trestle-packs"
    assert [_requirement_name(r) for r in project["dependencies"]] == ["trestle"]
    # the optional extras are unchanged: three third-party extras and their union
    assert set(project["optional-dependencies"]) == {"docker", "pytest", "migrate", "all"}
    assert project["optional-dependencies"]["docker"] == ["python-on-whales>=0.76"]


def test_packs_import_port_types_from_trestle_workflow() -> None:
    """A fresh interpreter with the two source trees on its path (the shape of the CI editable
    install) imports `trestle_packs` and, through it, the port types of `trestle.workflow`."""
    code = (
        "import trestle_packs.fakes as fakes\n"
        "from trestle.workflow import ports\n"
        "assert ports.ExecutionPort and ports.ResourceReads and ports.ResourceCreate\n"
        "assert ports.ResourceOwned and ports.InRunGroup and ports.BoundCommand\n"
        "assert fakes.FakeCommand and fakes.FakeMarker\n"
        "print('ok')\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": f"{ROOT}:{PACKS}",
    }
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
