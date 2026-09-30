"""Container-adapter tests: the packs `tests/` directory (shared fakes) and `tests/conformance` (the
family case modules) are put on `sys.path` (the packs tests have no package markers), and the
`rig` fixture drives the real adapters over the real `CommandPort` and the absolute-path
`fake_docker.py` shim: no real engine, nothing pulled, nothing started."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.proof.host.docker_gate import fake_docker

from trestle_packs.container.engine import DockerCli
from trestle_packs.process.command import CommandPort

_TESTS = Path(__file__).resolve().parent.parent
for _directory in (_TESTS, _TESTS / "conformance"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

ENDPOINT = "unix:///fake/desktop-linux.sock"


@dataclass
class Rig:
    """A docker CLI bound to the shim, its state file and the log of every call it received."""

    docker: DockerCli
    executable: str
    state: Path
    log: Path

    def write(self, **state: object) -> None:
        fake_docker.write_state(self.state, **state)

    def read(self) -> dict[str, Any]:
        return fake_docker.read_state(self.state)

    def calls(self) -> list[dict[str, Any]]:
        return fake_docker.read_log(self.log)

    def verbs(self) -> list[str]:
        return [c["args"][0] for c in self.calls() if c["args"]]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    state, log = tmp_path / "state.json", tmp_path / "log.jsonl"
    executable = fake_docker.install_shim(tmp_path, state, log)
    fake_docker.write_state(
        state,
        reachable=True,
        server_version="29.8.0",
        containers=[],
        images=[{"id": "sha256:aa", "repository": "alpine", "tag": "3.20", "repo_digests": []}],
        volumes=[],
        networks=[],
    )
    return Rig(DockerCli(executable, ENDPOINT, CommandPort()), executable, state, log)
