"""An engine that cannot be read blocks the node with its code, never fails it (L.RB-7.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.tree import treekit as tk
from trestle_packs.container import engine as adapter
from twin.twin_engine import SupportEngine, rig_over

from trestle_env import tree


def test_the_spelled_codes_are_the_adapters() -> None:
    assert tree.DOCKER_ENGINE_UNREACHABLE == adapter.DOCKER_ENGINE_UNREACHABLE
    assert tree.DOCKER_CLI_MISSING == adapter.DOCKER_CLI_MISSING


@pytest.mark.parametrize(
    ("knob", "code"),
    [
        ({"reachable": False}, tree.DOCKER_ENGINE_UNREACHABLE),
        ({"cli_present": False}, tree.DOCKER_CLI_MISSING),
    ],
)
def test_unreadable_engine_ends_blocked_with_its_code(
    tmp_path: Path, knob: dict, code: str
) -> None:
    engine = SupportEngine()
    for name, value in knob.items():
        setattr(engine, name, value)
    rig = rig_over(tmp_path, engine)
    rig.run()
    answer = tk.answer_of(rig)
    assert answer.outcome == "blocked"
    assert answer.primary.code == code
    assert answer.primary.human_action and tree.HTTP_SUPPORT_UNIT in answer.primary.human_action
    assert engine.effects == []  # nothing was created
