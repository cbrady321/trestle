"""Change 2d (owner decision 3): an agent following the agent-facing text alone never runs a shell
command for work Trestle does, nor a command that belongs to the service or its operator. Such a
command is quoted only for relay, on a line that says it is the operator's."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from trestle.query.catalog import view_catalog
from trestle.server import authoring

ROOT = Path(__file__).resolve().parents[3]
AGENT_FILES = (
    "skills/trestle/SKILL.md",
    ".cursor/skills/trestle/SKILL.md",
    "docs/agents.md",
    "docs/agent-console-mcp.md",
)
GUIDE = ROOT / "trestle" / "plugin" / "_guide"
OPERATOR_COMMAND = re.compile(
    r"\btrestle (service|init|doctor|recover|cancel|credentials|serve|pin|unpin|ops)\b|pip install"
)


def _texts() -> dict[str, str]:
    texts = {name: (ROOT / name).read_text(encoding="utf-8") for name in AGENT_FILES}
    for path in sorted(GUIDE.rglob("*")):
        if path.suffix in {".md", ".py"}:
            texts[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    texts["trestle://views"] = json.dumps(view_catalog(), indent=1)
    for restricted in (False, True):
        texts[f"server_instructions[{restricted}]"] = authoring.server_instructions(restricted)
    return texts


def _fenced(text: str) -> list[tuple[str, list[str]]]:
    """Each fenced block's info string and lines."""
    blocks: list[tuple[str, list[str]]] = []
    lines: list[str] | None = None
    info = ""
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if lines is None:
                info, lines = line.strip()[3:].strip(), []
            else:
                blocks.append((info, lines))
                lines = None
        elif lines is not None:
            lines.append(line)
    return blocks


@pytest.mark.parametrize("name", sorted(_texts()))
def test_no_fenced_trestle_command(name: str) -> None:
    for _, lines in _fenced(_texts()[name]):
        for line in lines:
            stripped = line.strip()
            assert not stripped.startswith(("trestle ", "$ trestle")), f"{name}: {line}"


@pytest.mark.parametrize("name", sorted(_texts()))
def test_no_host_command_config(name: str) -> None:
    for info, lines in _fenced(_texts()[name]):
        if info.startswith("json"):
            assert '"command":' not in "\n".join(lines), name


@pytest.mark.parametrize("name", sorted(_texts()))
def test_no_fall_back(name: str) -> None:
    assert "fall back" not in _texts()[name].lower(), name


@pytest.mark.parametrize("name", sorted(_texts()))
def test_operator_commands_are_the_operators(name: str) -> None:
    for line in _texts()[name].splitlines():
        if OPERATOR_COMMAND.search(line):
            assert "operator" in line.lower(), f"{name}: {line}"


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_skill_names_the_new_surface() -> None:
    skill = _flat(ROOT / "skills" / "trestle" / "SKILL.md")
    for needle in ('view="status"', "retry_after_s", "held_by", 'plugin="exec"', "session_ended"):
        assert needle in skill, needle
    assert "Never run it yourself, through `exec` or any other way." in skill


def test_the_cursor_skill_says_never_run_it() -> None:
    text = _flat(ROOT / ".cursor" / "skills" / "trestle" / "SKILL.md")
    assert "Never run it yourself, through `exec` or any other way." in text
