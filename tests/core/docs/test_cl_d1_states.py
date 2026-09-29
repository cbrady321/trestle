"""L.CL-D1.1: every documented run state has a producer, or is reserved (K-13).

The docs list the state vocabulary with each state's producer (`docs/agents.md`, "Run states"). A
state the docs list as produced must be produced by a fixture; a state the docs list as reserved
must be produced by nothing; and no code path may write a terminal state the docs do not list.
"""

from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

import pytest

from tests.proof import harness, records, tolerances
from trestle.server.ledger import TERMINAL_KINDS, RunLedger, ledger_path

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DOC = REPO_ROOT / "docs" / "agents.md"
CONSOLE_DOC = REPO_ROOT / "docs" / "agent-console-mcp.md"
S0_FOSSILS = REPO_ROOT / "tests" / "fixtures" / "fossils" / "s0"
SOURCE_DIRS = ("server", "wrapper", "child")

RESERVED = "reserved"
WORKER_EXIT_CODE = 3  # any code outside {0, 1} classifies as worker_exit
ROW = re.compile(
    r"^\|\s*`(?P<state>[a-z_]+)`\s*\|\s*(?P<kind>[^|]*?)\s*\|\s*(?P<producer>.*?)\s*\|$"
)


def documented_states(text: str) -> dict[str, tuple[str, str]]:
    """state -> (kind, producer) from the "Run states" table of `docs/agents.md`."""
    section = re.search(r"^## Run states\n(.*?)^(?:---|## )", text, re.S | re.M)
    if section is None:
        return {}
    states: dict[str, tuple[str, str]] = {}
    for line in section.group(1).splitlines():
        match = ROW.match(line.strip())
        if match:
            states[match["state"]] = (match["kind"], match["producer"])
    return states


def reserved_states(states: dict[str, tuple[str, str]]) -> set[str]:
    return {name for name, (_kind, producer) in states.items() if RESERVED in producer.lower()}


def console_states(text: str) -> set[str]:
    """The states `docs/agent-console-mcp.md` names in its "Run states" subsection."""
    section = re.search(r"^### Run states\n(.*?)^###? ", text, re.S | re.M)
    return set(re.findall(r"`([a-z_]+)`", section.group(1))) if section else set()


def source_producers(root: Path) -> set[str]:
    """Terminal states written by code under `root`: a string literal assigned to `classification`,
    or a literal terminal kind appended to a ledger."""
    produced: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and _is_literal(node.value):
                if any(isinstance(t, ast.Name) and t.id == "classification" for t in node.targets):
                    produced.add(str(node.value.value))  # type: ignore[attr-defined]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "append" and node.args and _is_literal(node.args[0]):
                    kind = str(node.args[0].value)  # type: ignore[attr-defined]
                    if kind in TERMINAL_KINDS:
                        produced.add(kind)
    return produced


def _is_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def fixture_states() -> set[str]:
    """Every state the committed S0 fossils (today's kernel driven to each state) project into."""
    seen: set[str] = set()
    for run_dir in sorted(S0_FOSSILS.glob("*/home/runs/*/r_*")):
        seen.add(RunLedger.open(ledger_path(run_dir)).projected_state())
        terminal = records.node_record(run_dir).terminal
        if terminal is not None:
            seen.add(terminal)
    return seen


def worker_exit_state() -> str:
    """A live run of a plugin that exits with code 3, read back through the strict oracle."""
    with tempfile.TemporaryDirectory(prefix="cl-d1-states-") as tmp:
        plugins = Path(tmp) / "plugins"
        plugins.mkdir()
        (plugins / "exit_three.py").write_text(
            "from trestle.plugin.surface import Context, trestle\n\n\n"
            "@trestle\n"
            "def exit_three(ctx: Context) -> dict[str, bool]:\n"
            f"    raise SystemExit({WORKER_EXIT_CODE})\n",
            encoding="utf-8",
        )
        kernel = harness.fresh_kernel(plugin_dirs=[plugins], home=Path(tmp) / "home")
        run_dir = harness.run_to_dir(kernel, "exit_three", {}, wait_ms=tolerances.HARNESS_WAIT_MS)
        return records.node_record(run_dir).terminal or "none"


def violations(
    states: dict[str, tuple[str, str]],
    produced_by_fixtures: set[str],
    produced_by_source: set[str],
) -> list[str]:
    """Why the documented vocabulary is not "each state produced, or reserved"; empty when it is."""
    problems: list[str] = []
    reserved = reserved_states(states)
    for name in sorted(TERMINAL_KINDS - set(states)):
        problems.append(f"terminal kind {name!r} is not documented")
    for name in sorted(set(states) - reserved - produced_by_fixtures):
        problems.append(
            f"documented state {name!r} has no fixture producing it and is not reserved"
        )
    for name in sorted(reserved & (produced_by_fixtures | produced_by_source)):
        problems.append(f"state {name!r} is marked reserved but something produces it")
    for name in sorted(produced_by_source - set(states)):
        problems.append(f"undocumented producer writes state {name!r}")
    return problems


def _vocabulary_problems() -> list[str]:
    states = documented_states(AGENTS_DOC.read_text(encoding="utf-8"))
    produced_by_source: set[str] = set()
    for sub in SOURCE_DIRS:
        produced_by_source |= source_producers(REPO_ROOT / "trestle" / sub)
    produced_by_fixtures = fixture_states() | {worker_exit_state()}
    return violations(states, produced_by_fixtures, produced_by_source)


@pytest.mark.proves(
    "WR-EVID-9",
    "WR-EVID-9:documented-states-produced-or-reserved",
    "core",
    "core",
    "PROC+INSPECT",
    "CI",
)
def test_each_documented_state_has_verifier_or_reserved() -> None:
    states = documented_states(AGENTS_DOC.read_text(encoding="utf-8"))
    expected = {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "worker_exit",
        "interrupted",
        "crashed",
    }
    assert set(states) == expected, set(states) ^ expected
    assert reserved_states(states) == {"crashed"}
    assert set(states) >= TERMINAL_KINDS  # TERMINAL_KINDS itself is untouched (BFD-09 Leave)
    assert console_states(CONSOLE_DOC.read_text(encoding="utf-8")) == expected

    produced = fixture_states() | {worker_exit_state()}
    assert produced >= expected - {"crashed"}, expected - {"crashed"} - produced
    assert "crashed" not in produced
    assert _vocabulary_problems() == []


def test_planted_undocumented_producer_fails(tmp_path: Path) -> None:
    states = documented_states(AGENTS_DOC.read_text(encoding="utf-8"))
    produced = fixture_states()

    planted = tmp_path / "planted.py"
    planted.write_text('classification = "vanished"\n', encoding="utf-8")
    assert source_producers(tmp_path) == {"vanished"}
    assert violations(states, produced, source_producers(tmp_path)) == [
        "undocumented producer writes state 'vanished'"
    ]

    planted.write_text('ledger.append("crashed", run_id="r_x")\n', encoding="utf-8")
    problems = violations(states, produced, source_producers(tmp_path))
    assert problems == ["state 'crashed' is marked reserved but something produces it"]


def test_planted_documentation_gaps_fail() -> None:
    text = AGENTS_DOC.read_text(encoding="utf-8")
    states = documented_states(text)
    produced = fixture_states() | {"worker_exit"}

    missing = {k: v for k, v in states.items() if k != "interrupted"}
    assert violations(missing, produced, set()) == ["terminal kind 'interrupted' is not documented"]

    unreserved = dict(states, crashed=("terminal", "The conductor."))
    assert violations(unreserved, produced, set()) == [
        "documented state 'crashed' has no fixture producing it and is not reserved"
    ]
