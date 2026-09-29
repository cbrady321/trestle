"""K-6 (L.NW-1.1): docs/packs.md states each teardown value's removal set, and it equals what the
runner asks the compose backend to do (WR-OWN-4:docs-equal-code-fake)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fake_compose_backend import FakeComposeBackend
from fake_pack_context import FakePackContext

from trestle_packs.docker.runner import StackRunner
from trestle_packs.docker.spec import StackSpec

DOCS = Path(__file__).resolve().parents[4] / "docs" / "packs.md"
REMOVED = {"containers", "networks", "volumes"}


def _documented_removal_sets() -> dict[str, set[str]]:
    """Rows of the `### Teardown` table: first-column value -> the `Removes` column's set."""
    text = DOCS.read_text(encoding="utf-8")
    section = text.split("### Teardown", 1)[1].split("\n### ", 1)[0]
    rows: dict[str, set[str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or not cells[0].startswith("`"):
            continue
        value = cells[0].strip("`").split("=")[0]
        rows[value] = {w.strip() for w in cells[2].split(",")} & REMOVED
    return rows


def _observed_removal_set(tmp_path: Path, teardown: str, *, reset: bool) -> set[str]:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    backend = FakeComposeBackend()
    runner = StackRunner(FakePackContext(work=tmp_path), backend=backend)
    spec = StackSpec.from_dict(
        {
            "compose_file": "docker-compose.yml",
            "teardown": teardown,
            "waves": [{"name": "app", "services": ["web"], "wait": "started", "timeout_s": 5}],
        }
    )
    runner.down(spec, cwd=tmp_path, reset_volumes=reset)
    assert backend.stop_calls == (1 if teardown == "stop" and not reset else 0)
    if not backend.down_calls:
        return set()
    (remove_volumes,) = backend.down_calls
    return {"containers", "networks"} | ({"volumes"} if remove_volumes else set())


def test_documented_removal_sets_match_code(tmp_path: Path) -> None:
    documented = _documented_removal_sets()
    assert set(documented) == {"down", "stop", "none", "reset_volumes"}
    assert documented["down"] == _observed_removal_set(tmp_path, "down", reset=False)
    assert documented["stop"] == _observed_removal_set(tmp_path, "stop", reset=False)
    assert documented["none"] == _observed_removal_set(tmp_path, "none", reset=False)
    assert documented["reset_volumes"] == _observed_removal_set(tmp_path, "down", reset=True)
    # only the explicit reset removes volumes
    assert [v for v, removed in documented.items() if "volumes" in removed] == ["reset_volumes"]


@pytest.mark.parametrize("policy", ["down", "stop", "none"])
def test_reset_volumes_is_the_only_volume_removing_path(tmp_path: Path, policy: str) -> None:
    assert "volumes" not in _observed_removal_set(tmp_path, policy, reset=False)
    assert "volumes" in _observed_removal_set(tmp_path, policy, reset=True)


def test_reset_volumes_not_exposed_by_entry_point_schemas() -> None:
    root = Path(__file__).resolve().parents[4]
    for path in (root / "examples" / "packs").glob("*.py"):
        assert "reset_volumes" not in path.read_text(encoding="utf-8"), path.name
