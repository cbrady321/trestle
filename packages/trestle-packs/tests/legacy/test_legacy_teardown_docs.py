"""K-6 (L.NW-1.1): docs/packs.md states each teardown value's removal set, and it equals what
`docker_stack` asks docker to do (WR-OWN-4:docs-equal-code-fake).

Each row is observed on the fake docker CLI (`trestle_pack_docker.fake_cli`, Compose's
semantics): a stack of one service with a named volume comes up in a first wave, the second wave
fails, and the declared teardown runs; a kind is removed when every object of it is gone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pack_wrappers import WRAPPERS, compose, context, fake_docker, load_wrapper
from trestle.plugin import Failed
from trestle.plugin._codec import encode
from trestle.plugin.testing import call_plugin

# K-3/K-4: these plugins take and return typed records (skipped if they are declined).
pytestmark = pytest.mark.typed_records

DOCS = Path(__file__).resolve().parents[4] / "docs" / "packs.md"
REMOVED = {"containers", "networks", "volumes"}
COMPOSE = (
    "services:\n  keeper:\n    image: alpine:3.20\n    volumes:\n      - data:/data\n"
    "  broken:\n    image: alpine:3.20\nvolumes:\n  data:\n"
)


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


def _observed_removal_set(tmp_path: Path, teardown: str) -> set[str]:
    monkeypatch = pytest.MonkeyPatch()
    try:
        work = tmp_path / "work"
        compose(work, COMPOSE)
        fake = fake_docker(tmp_path, monkeypatch, fail=["broken"])
        spec = {
            "project": "k6",
            "teardown": teardown,
            "waves": [
                {"name": "keep", "services": ["keeper"], "wait": "started"},
                {"name": "broken", "services": ["broken"], "wait": "started"},
            ],
        }
        with pytest.raises(Failed) as failed:  # the `broken` wave fails: the stack is torn down
            call_plugin(
                load_wrapper("docker_stack").docker_stack,
                {"spec": spec, "workdir": str(work)},
                ctx=context(tmp_path),
            )
        answer = json.loads(encode(failed.value.result))
        assert isinstance(answer, dict) and answer["teardown"] == teardown, answer
        state = fake.state
    finally:
        monkeypatch.undo()
    present = {
        "containers": [n for n, s in state["containers"].items() if s in ("running", "exited")],
        "networks": state["networks"],
        "volumes": list(state["volumes"]),
    }
    return {kind for kind, names in present.items() if not names}


@pytest.mark.proves("WR-OWN-4", "WR-OWN-4:docs-equal-code-fake", "B", "B", "LOGIC", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-6", "B", "B", "LOGIC", "CI")
def test_documented_removal_sets_match_code(tmp_path: Path) -> None:
    documented = _documented_removal_sets()
    assert set(documented) == {"down", "stop", "none"}
    for row in documented:
        assert documented[row] == _observed_removal_set(tmp_path / row, row), row
    # no teardown removes volumes
    assert [v for v, removed in documented.items() if "volumes" in removed] == []


def test_no_wrapper_removes_volumes() -> None:
    for name, folder in WRAPPERS.items():
        path = folder / f"{name}.py"
        source = path.read_text(encoding="utf-8")
        assert '"--volumes"' not in source and '"-v"' not in source, path.name
        assert "reset_volumes" not in source, path.name
