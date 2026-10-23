"""The K-6 teardown table, read from docs/packs.md and observed on an engine (L.RB-3.4; WR-OWN-4).

Shared by the HOST node (`host/test_k6_docs.py`, the operator's docker) and its CI twin
(`twin/test_k6_docs_twin.py`, the fake docker CLI `trestle_packs.fakes.docker_cli`), so the two
parse the SAME rows and derive the removal set the SAME way; only the engine differs.

Each documented row is one teardown case run on a fresh stack of one service, the default network
and one named volume (every object labelled `trestle.proof.fixture=k6-docs`, CSC-10): bring it up
with `docker_stack` (examples/packs, called with `FakeContext`), run the wrapper's own `teardown`
step with the row's value, and read which kinds of object it took away. A kind is removed when
every object of it that existed before the teardown is gone after it; a stopped container is still
a container.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Protocol

from trestle.plugin.testing import FakeContext, call_plugin
from trestle_packs.fakes.docker_cli import read_state, write_fake_docker

REPO = Path(__file__).resolve().parents[4]
DOCS = REPO / "docs" / "packs.md"
DOCKER_STACK = REPO / "examples" / "packs" / "docker_stack.py"
KINDS = ("containers", "networks", "volumes")
FIXTURE_LABEL = "trestle.proof.fixture"
FIXTURE = "k6-docs"
SERVICE = "keeper"
VOLUME = "data"

# the documented rows: each is a declared teardown value
CASES = ("down", "stop", "none")


def documented_removal_sets(docs: Path = DOCS) -> dict[str, frozenset[str]]:
    """Rows of the `### Teardown` table: the first column's value -> the `Removes` column's set."""
    text = docs.read_text(encoding="utf-8")
    section = text.split("### Teardown", 1)[1].split("\n### ", 1)[0]
    rows: dict[str, frozenset[str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or not cells[0].startswith("`"):
            continue
        value = cells[0].strip("`").split("=")[0]
        rows[value] = frozenset(w.strip() for w in cells[2].split(",")) & frozenset(KINDS)
    return rows


def compose_definition(image: str) -> str:
    """One long-running service with a named volume; every object carries the fixture label."""
    return (
        "services:\n"
        f"  {SERVICE}:\n"
        f"    image: {image}\n"
        "    pull_policy: never\n"
        '    command: ["sh", "-c", "trap \'exit 0\' TERM; while :; do sleep 1; done"]\n'
        "    volumes:\n"
        f"      - {VOLUME}:/data\n"
        "    labels:\n"
        f"      {FIXTURE_LABEL}: {FIXTURE}\n"
        "volumes:\n"
        f"  {VOLUME}:\n"
        "    labels:\n"
        f"      {FIXTURE_LABEL}: {FIXTURE}\n"
        "networks:\n"
        "  default:\n"
        "    labels:\n"
        f"      {FIXTURE_LABEL}: {FIXTURE}\n"
    )


class Engine(Protocol):
    """What a case needs of an engine: the docker CLI the wrapper runs, and its objects."""

    def docker_path(self) -> str: ...

    def objects(self, project: str) -> dict[str, frozenset[str]]: ...


def load_docker_stack() -> ModuleType:
    spec = importlib.util.spec_from_file_location("k6_docs_docker_stack", DOCKER_STACK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def removal_set(before: dict[str, frozenset[str]], after: dict[str, frozenset[str]]) -> frozenset:
    """The kinds whose every object present before the teardown is gone after it."""
    return frozenset(k for k in KINDS if before[k] and not before[k] & after[k])


def observe_case(
    engine: Engine,
    workdir: Path,
    project: str,
    row: str,
    image: str,
    timeout_s: float,
    monkeypatch: object,
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Up, then the row's teardown; the engine's objects of `project` before and after it."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "compose.yaml").write_text(compose_definition(image), encoding="utf-8")
    monkeypatch.setenv("TRESTLE_DOCKER_PATH", engine.docker_path())  # type: ignore[attr-defined]
    module = load_docker_stack()
    spec = {
        "project": project,
        "compose_file": "compose.yaml",
        "teardown": row,
        "waves": [
            {"name": "keep", "services": [SERVICE], "wait": "started", "timeout_s": timeout_s}
        ],
    }
    ctx = FakeContext(workdir / "ctx")
    answer = call_plugin(module.docker_stack, {"spec": spec, "workdir": str(workdir)}, ctx=ctx)
    assert isinstance(answer, dict) and answer["ok"] is True, answer
    before = engine.objects(project)
    stack = module.DockerSpec(project=project, compose_file="compose.yaml", teardown=row)
    compose = module.compose_command(engine.docker_path(), stack, workdir)
    assert module.teardown(ctx, compose, row, workdir) == row
    return before, engine.objects(project)


def assert_docs_equal_observed(
    observe: Callable[[str], tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]],
) -> None:
    """Every documented row equals the removal set observed for it, and none removes a volume."""
    documented = documented_removal_sets()
    assert set(documented) == set(CASES), documented
    observed = {}
    for row in CASES:
        before, after = observe(row)
        assert all(before[k] for k in KINDS), (row, before)  # the stack really had each kind
        observed[row] = removal_set(before, after)
    assert observed == documented, (observed, documented)
    assert [row for row, kinds in observed.items() if "volumes" in kinds] == []


# -- the twin's engine ----------------------------------------------------------------------------


class FakeEngine:
    """The fake docker CLI over a JSON state file: Compose's documented semantics (`up` creates
    the service containers, the project's default network and its named volumes; `down` removes
    containers and networks, never volumes without `--volumes`; `stop` removes nothing)."""

    def __init__(self, base: Path) -> None:
        self.state = base / "engine.json"
        self._docker = write_fake_docker(base / "bin", self.state)

    def docker_path(self) -> str:
        return str(self._docker)

    def calls(self) -> list[str]:
        return [
            c for c in read_state(self.state)["calls"] if c.split()[0] in ("up", "stop", "down")
        ]

    def objects(self, project: str) -> dict[str, frozenset[str]]:
        state = read_state(self.state)
        return {
            "containers": frozenset(n for n in state["containers"] if n.startswith(f"{project}-")),
            "networks": frozenset(n for n in state["networks"] if n.startswith(f"{project}_")),
            "volumes": frozenset(n for n in state["volumes"] if n.startswith(f"{project}_")),
        }
