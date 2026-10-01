"""The K-6 teardown table, read from docs/packs.md and observed on an engine (L.RB-3.4; WR-OWN-4).

Shared by the HOST node (`host/test_k6_docs.py`, real Docker through the legacy python-on-whales
backend) and its CI twin (`twin/test_k6_docs_twin.py`, `EngineComposeBackend` below), so the two
parse the SAME rows and derive the removal set the SAME way; only the engine differs.

Each documented row is one teardown case run on a fresh stack of one service, the default network
and one named volume (every object labelled `trestle.proof.fixture=k6-docs`, CSC-10): bring it up
with `StackRunner.up`, tear it down the way the row names (`StackRunner.down`, with
`reset_volumes=True` for the `reset_volumes` row), and read which kinds of object the teardown
took away. A kind is removed when every object of it that existed before the teardown is gone
after it; a stopped container is still a container.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from trestle_packs.docker.runner import StackRunner
from trestle_packs.docker.spec import StackSpec, WaitMode

REPO = Path(__file__).resolve().parents[4]
DOCS = REPO / "docs" / "packs.md"
KINDS = ("containers", "networks", "volumes")
FIXTURE_LABEL = "trestle.proof.fixture"
FIXTURE = "k6-docs"
SERVICE = "keeper"
VOLUME = "data"

# row -> (the declared teardown policy, reset_volumes)
CASES: dict[str, tuple[str, bool]] = {
    "down": ("down", False),
    "stop": ("stop", False),
    "none": ("none", False),
    "reset_volumes": ("down", True),
}


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
    """What a case needs of an engine: the legacy backend bound to it, and its objects."""

    def backend(self) -> Any: ...

    def objects(self, project: str) -> dict[str, frozenset[str]]: ...


@dataclass
class Context:
    """The minimal pack context `StackRunner` logs through (no artifact is attached here)."""

    work: Path
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def progress(self, message: str, *, fraction: float | None = None) -> None:
        self.logs.append(message)

    def artifact(self, name: str) -> Path:
        path = self.work / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def attach(self, path: Path, *, name: str) -> str:
        return f"art_{name}"


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
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Up, then the row's teardown; the engine's objects of `project` before and after it."""
    policy, reset = CASES[row]
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "compose.yaml").write_text(compose_definition(image), encoding="utf-8")
    spec = StackSpec.from_dict(
        {
            "project": project,
            "compose_file": "compose.yaml",
            "teardown": policy,
            "waves": [
                {
                    "name": "keep",
                    "services": [SERVICE],
                    "wait": WaitMode.STARTED.value,
                    "timeout_s": timeout_s,
                }
            ],
        }
    )
    runner = StackRunner(Context(work=workdir), backend=engine.backend())
    runner.up(spec, cwd=workdir)
    before = engine.objects(project)
    runner.down(spec, cwd=workdir, reset_volumes=reset)
    return before, engine.objects(project)


def assert_docs_equal_observed(
    observe: Callable[[str], tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]],
) -> None:
    """Every documented row equals the removal set observed for it, and only the explicit
    reset removes a volume."""
    documented = documented_removal_sets()
    assert set(documented) == set(CASES), documented
    observed = {}
    for row in CASES:
        before, after = observe(row)
        assert all(before[k] for k in KINDS), (row, before)  # the stack really had each kind
        observed[row] = removal_set(before, after)
    assert observed == documented, (observed, documented)
    assert [row for row, kinds in observed.items() if "volumes" in kinds] == ["reset_volumes"]


# -- the twin's engine ----------------------------------------------------------------------------


class EngineComposeBackend:
    """A `ComposeBackend` over an in-memory engine that applies Compose's documented semantics:
    `up` creates the service containers, the project's default network and its named volumes;
    `down` removes containers and networks (and the volumes only with `remove_volumes`); `stop`
    stops the containers and removes nothing."""

    def __init__(self) -> None:
        self.state: dict[str, dict[str, dict[str, str]]] = {}
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def _project(self, spec: StackSpec) -> dict[str, dict[str, str]]:
        return self.state.setdefault(
            str(spec.project), {"containers": {}, "networks": {}, "volumes": {}}
        )

    def up(self, spec: StackSpec, services: list[str], **_: Any) -> None:
        self.calls.append("up")
        objs = self._project(spec)
        for service in services:
            objs["containers"][f"{spec.project}-{service}-1"] = "running"
        objs["networks"][f"{spec.project}_default"] = "present"
        objs["volumes"][f"{spec.project}_{VOLUME}"] = "present"

    def down(self, spec: StackSpec, *, cwd: Path, remove_volumes: bool = False) -> None:
        self.calls.append(f"down(volumes={remove_volumes})")
        objs = self._project(spec)
        objs["containers"].clear()
        objs["networks"].clear()
        if remove_volumes:
            objs["volumes"].clear()

    def stop(self, spec: StackSpec, *, cwd: Path) -> None:
        self.calls.append("stop")
        objs = self._project(spec)
        for name in objs["containers"]:
            objs["containers"][name] = "exited"

    def service_logs(self, spec: StackSpec, services: list[str], *, cwd: Path) -> dict[str, str]:
        return {service: "" for service in services}


class FakeEngine:
    def __init__(self) -> None:
        self._backend = EngineComposeBackend()

    def backend(self) -> EngineComposeBackend:
        return self._backend

    def objects(self, project: str) -> dict[str, frozenset[str]]:
        objs = self._backend.state.get(project, {})
        return {k: frozenset(objs.get(k, {})) for k in KINDS}
